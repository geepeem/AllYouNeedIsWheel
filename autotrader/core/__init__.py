"""
AutoTrader Core Module
"""

# Import tools and utilities
from .utils import (
    setup_logging, 
    rotate_logs, 
    rotate_reports, 
    get_closest_friday, 
    get_next_monthly_expiration
)

# Import connection classes
from .connection import IBConnection, Option

# Import processing classes
from .processing import (
    SimpleOptionsStrategy,
    print_stock_summary,
    open_in_browser,
    format_currency,
    format_percentage
)

__all__ = [
    # Connection
    'IBConnection',
    
    # Export
    'export_options_data', 
    'export_to_csv', 
    'export_to_html', 
    'create_combined_html_report',
    
    # Utils
    'rotate_logs',
    'rotate_reports',
    'setup_logging',
    'get_closest_friday',
    'get_next_monthly_expiration',
    'parse_date_string',
    'format_date_string',
    
    # Processing
    'process_stock',
    'print_stock_summary',
    'export_all_stocks_data',
    'get_strikes_around_price',
    'open_in_browser',
    'SimpleOptionsStrategy',
    'format_currency',
    'format_percentage'
] 