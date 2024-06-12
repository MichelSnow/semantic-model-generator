from typing import List

import sqlglot
import sqlglot.expressions
from sqlglot.dialects.snowflake import Snowflake

from semantic_model_generator.protos import semantic_model_pb2

_LOGICAL_TABLE_PREFIX = "__"


def is_logical_table(table_name: str) -> bool:
    """Returns true if 'table_name' is a logical table name."""
    return table_name.startswith(_LOGICAL_TABLE_PREFIX) and len(table_name) > len(
        _LOGICAL_TABLE_PREFIX
    )


def logical_table_name(table: semantic_model_pb2.Table) -> str:
    """Returns the name of logical table for a given table.  E.g. __fact"""
    return _LOGICAL_TABLE_PREFIX + table.name  # type: ignore[no-any-return]


def fully_qualified_table_name(table: semantic_model_pb2.FullyQualifiedTable) -> str:
    """Returns fully qualified table name such as my_db.my_schema.my_table"""
    fqn = table.table
    if len(table.schema) > 0:
        fqn = f"{table.schema}.{fqn}"
    if len(table.database) > 0:
        fqn = f"{table.database}.{fqn}"
    return fqn  # type: ignore[no-any-return]


def is_aggregation_expr(col: semantic_model_pb2.Column) -> bool:
    """Check if an expr contains aggregation function.
    Note: only flag True for aggregations that would changes number of rows of data.
    For window function, given the operation will produce value per row, mark as False here.

    Raises:
        ValueError: if expr is not parsable, or if aggregation expressions in non-measure columns.
    """
    parsed = sqlglot.parse_one(col.expr, dialect="snowflake")
    agg_func = list(parsed.find_all(sqlglot.expressions.AggFunc))
    window = list(parsed.find_all(sqlglot.expressions.Window))
    # We've confirmed window functions cannot appear inside aggregate functions
    # (gets execution error msg: Window function [SUM(...) OVER (PARTITION BY ...)] may not appear inside an aggregate function).
    # So if there's a window function present there can't also be an aggregate function applied to the window function.
    if len(agg_func) > 0 and len(window) == 0:
        if col.kind != 2:
            raise ValueError("Only allow aggregation expressions for measures.")
        return True
    return False


def remove_ltable_cte(sql_w_ltable_cte: str) -> str:
    """Given a sql with prefix'd logical table conversion CTE,
    return:
        sql_without_logical_cte: the sql without the logical table conversion CTE.

    Raises:
        ValueError: If didn't find any CTE or parsed first CTE is not logical table CTE.
    """
    ast = sqlglot.parse_one(sql_w_ltable_cte, read=Snowflake)
    with_ = ast.args.get("with")
    if with_ is None:
        raise ValueError("Must contain the logical CTE.")
    if not is_logical_table(with_.expressions[0].alias):
        raise ValueError("Must contain the logical CTE.")

    if len(with_.expressions) == 1:
        # If only one cte, remove full with clause
        with_.pop()
    else:
        # Otherwise simply remove the first cte.
        with_.expressions[0].pop()
    sql_without_logical_cte = ast.sql(dialect=Snowflake, pretty=True)
    return sql_without_logical_cte  # type: ignore [no-any-return]


def _generate_cte_for(table: semantic_model_pb2.Table) -> str:
    """
    Returns a CTE representing a logical table that selects 'col' columns from 'table'.
    """

    def _get_col_expr(column: semantic_model_pb2.Column) -> str:
        return (
            f"{column.expr} as {column.name}"
            if column.expr.lower() != column.name.lower()
            else f"{column.expr}"
        )

    columns = []
    table_non_agg_column_names = {
        col.name: col for col in table.columns if not is_aggregation_expr(col)
    }
    # If a table has no explicit columns referenced (e.g. for select count(*) from table)
    # or "*" is in the columns (e.g. for select * from table),
    # then just add all the columns (excl. ones with aggregation functions in expr).
    columns.extend(
        [
            _get_col_expr(col)
            for col in table.columns
            if col.name in table_non_agg_column_names
        ]
    )

    cte = f"WITH {logical_table_name(table)} AS (\n"
    cte += "SELECT \n"
    cte += ",\n".join(columns) + "\n"
    cte += f"FROM {fully_qualified_table_name(table.base_table)}"
    cte += ")"
    return cte


def expand_all_logical_tables_as_ctes(
    sql_query: str, model: semantic_model_pb2.SemanticModel
) -> str:
    """
    Returns a SQL query that expands all logical tables contained in ctx as ctes.
    """

    def generate_full_logical_table_ctes(
        ctx: semantic_model_pb2.SemanticModel,
    ) -> List[str]:
        """
        Given an arbitrary SQL, returns a list of CTEs representing all the logical tables
        referenced in it.
        """
        ctes: List[str] = []
        for table in ctx.tables:
            # If table contains expr with aggregations, we need to select the referred columns within CTE.
            # Enrich the table with the referred columns, if not listed explicitly within table.columns.
            # table = _enrich_column_in_expr_with_aggregation(table)
            # Append all columns and expressions for the logical table.
            ctes.append(_generate_cte_for(table))
        return ctes

    # convert semantic model into Column format to be compatible with old utils.
    ctx = context_to_column_format(model)

    # Step 1: Generate a CTE for each logical table referenced in the query.
    ctes = generate_full_logical_table_ctes(ctx)

    # Step 2: Parse each generated CTE as a 'WITH' clause.
    new_withs = []
    for cte in ctes:
        new_withs.append(
            sqlglot.parse_one(cte, read=Snowflake, into=sqlglot.expressions.With)
        )

    # Step 3: Prefix the CTEs to the original query.
    ast = sqlglot.parse_one(sql_query, read=Snowflake)
    with_ = ast.args.get("with")
    # If the query doesn't have a WITH clause, then generate one.
    if with_ is None:
        merged_with = new_withs[0]
        remaining_ctes = [w.expressions[0] for w in new_withs[1:]]
        merged_with.set("expressions", merged_with.expressions + remaining_ctes)
        ast.set("with", merged_with)
    # If the query already has a WITH clause, prefix the CTEs to it.
    else:
        new_ctes = [w.expressions[0] for w in new_withs]
        with_.set("expressions", new_ctes + with_.expressions)
    return ast.sql(dialect=Snowflake, pretty=True)  # type: ignore [no-any-return]


def context_to_column_format(
    ctx: semantic_model_pb2.SemanticModel,
) -> semantic_model_pb2.SemanticModel:
    """
    Converts semantic_model_pb2.SemanticModel from a dimension/measure format to a column format.
    Returns a new semantic_model_pb2.SemanticModel object that's in column format.
    """
    ret = semantic_model_pb2.SemanticModel()
    ret.CopyFrom(ctx)
    for table in ret.tables:
        column_format = len(table.columns) > 0
        dimension_measure_format = (
            len(table.dimensions) > 0
            or len(table.time_dimensions) > 0
            or len(table.measures) > 0
        )
        if column_format and dimension_measure_format:
            raise ValueError(
                "table {table.name} defines both columns and dimensions/time_dimensions/measures."
            )
        if column_format:
            continue
        for d in table.dimensions:
            col = semantic_model_pb2.Column()
            col.kind = semantic_model_pb2.ColumnKind.dimension
            col.name = d.name
            col.synonyms.extend(d.synonyms)
            col.description = d.description
            col.expr = d.expr
            col.data_type = d.data_type
            col.unique = d.unique
            col.sample_values.extend(d.sample_values)
            table.columns.append(col)
        del table.dimensions[:]

        for td in table.time_dimensions:
            col = semantic_model_pb2.Column()
            col.kind = semantic_model_pb2.ColumnKind.time_dimension
            col.name = td.name
            col.synonyms.extend(td.synonyms)
            col.description = td.description
            col.expr = td.expr
            col.data_type = td.data_type
            col.unique = td.unique
            col.sample_values.extend(td.sample_values)
            table.columns.append(col)
        del table.time_dimensions[:]

        for m in table.measures:
            col = semantic_model_pb2.Column()
            col.kind = semantic_model_pb2.ColumnKind.measure
            col.name = m.name
            col.synonyms.extend(m.synonyms)
            col.description = m.description
            col.expr = m.expr
            col.data_type = m.data_type
            col.default_aggregation = m.default_aggregation
            col.sample_values.extend(m.sample_values)
            table.columns.append(col)
        del table.measures[:]
    return ret