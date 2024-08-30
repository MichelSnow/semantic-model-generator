from typing import Any, Optional

import streamlit as st
import pandas as pd

from semantic_model_generator.data_processing.proto_utils import proto_to_dict


class CortexDimension:
    """
    Class for Cortex dimension-type field.
    """

    def __init__(self,
                 data: dict[str, Any]):
        
        self.data = data
        self.name = data['name']
        self.synonyms = data.get('synonyms', None)
        self.data_type = data.get('data_type', 'TEXT')
        self.expr = data['expr']
        self.description = data.get('description', None)
        self.sample_values = data.get('sample_values', None)
        self.unique = data.get('unique', False)

    def get_name(self) -> str:
        return self.name

    def get_data(self) -> dict[str, Any]:
        return self.data
    
    def get_cortex_type(self) -> str:
        return self.data_type
    
    def get_description(self) -> Optional[str]:
        return self.description
    
    def set_description(self, value) -> None:
        self.description = value
    
    def get_cortex_section(self) -> str:
        return 'dimensions'
    
    def get_key(self) -> str:
        return self.expr.upper()
    
    def get_cortex_details(self) -> dict[str, Any]:
        """
        Used in static methods in partner classes to retrieve and modify Cortex-equivalent details
        """
        return self.data
    
    def get_cortex_comparison_dict(self) -> dict[str, Any]:
        return {
            'field_key': self.get_key(),
            'section': self.get_cortex_section(),
            'field_details': self.get_cortex_details()
        }
    

class CortexTimeDimension(CortexDimension):
    """
    Class for Cortex time dimension-type field.
    """
        
    def get_cortex_section(self) -> str:
        return 'time_dimensions'
    

class CortexMeasure(CortexDimension):
    """
    Class for Cortex measure-type field.
    """
        
    def __init__(self, entity):
        super().__init__(entity)
        self.default_aggregation = entity.get('default_aggregation', None)

    def get_cortex_section(self) -> str:
        return 'measures'



class CortexSemanticTable:
    """
    Class for single Cortex logical table in semantic file.
    """

    def __init__(self,
                 data: dict[str, Any]):
        self.data = data
        self.name = data['name']
        self.description = data['description']
        self.base_table_db = data['base_table']['database']
        self.base_table_schema = data['base_table']['schema']
        self.base_table_table = data['base_table']['table']
        self.dimensions = data['dimensions']
        self.time_dimensions = data['time_dimensions']
        self.measures = data['measures']
    
    def get_data(self) -> dict[str, Any]:
        return self.data
    
    def get_name(self) -> str:
        return self.name
    
    def get_description(self) -> Optional[str]:
        return self.description
    
    def get_cortex_fields(self) -> list[CortexDimension | CortexTimeDimension | CortexMeasure]:
        """
        Processes and returns raw field data as vendor-specific field objects.
        """
        
        cortex_fields = []
        for dimension in self.dimensions:
            cortex_fields.append(CortexDimension(dimension).get_cortex_comparison_dict())
        for time_dimension in self.time_dimensions:
            cortex_fields.append(CortexTimeDimension(time_dimension).get_cortex_comparison_dict())
        for measure in self.measures:
            cortex_fields.append(CortexMeasure(measure).get_cortex_comparison_dict())
        
        return cortex_fields
        
    def create_comparison_df(self) -> pd.DataFrame:
        cortex_fields = self.get_cortex_fields()
        return pd.DataFrame(cortex_fields)
    
    @staticmethod
    def create_cortex_table_list() -> None:
        cortex_semantic = proto_to_dict(st.session_state["semantic_model"])
        # Need to replace table details in current entire yaml
        st.session_state['current_yaml_as_dict'] = cortex_semantic
        tables = []
        for table in cortex_semantic['tables']:
            tables.append(CortexSemanticTable(table))
        st.session_state['cortex_comparison_tables'] = tables

    @staticmethod
    def retrieve_df_by_name(name: str) -> pd.DataFrame:
        for table in st.session_state["cortex_comparison_tables"]:
            if table.get_name() == name:
                return table.create_comparison_df()