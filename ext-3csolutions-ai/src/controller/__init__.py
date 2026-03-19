"""
Controller layer for the document extraction pipeline.

Provides the main entry point controller that orchestrates
the entire document processing workflow.
"""

from .data_controller import DataController

__all__ = [
    'DataController',
]
