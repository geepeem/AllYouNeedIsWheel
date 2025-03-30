"""
Stock and Options Trading Connection Module for Interactive Brokers
"""

import logging
import asyncio
import math
import time
import os
import json
import threading
import traceback
from typing import Optional, Dict, Any
from datetime import datetime
import pytz
from core.utils import is_market_hours

# Import ib_insync
from ib_insync import IB, Stock, Option, Contract, util

# Import our logging configuration
from core.logging_config import get_logger

# Configure logging
logger = get_logger('autotrader.connection', 'tws')

# Set ib_insync logger to WARNING level to reduce noise
def suppress_ib_logs():
    """
    Suppress verbose logs from the ib_insync library by setting higher log levels
    """
    # Base ib_insync loggers
    logging.getLogger('ib_insync').setLevel(logging.WARNING)
    logging.getLogger('ib_insync.wrapper').setLevel(logging.WARNING)
    logging.getLogger('ib_insync.client').setLevel(logging.WARNING)
    logging.getLogger('ib_insync.ticker').setLevel(logging.WARNING)
    
    # Additional ib_insync logger components
    logging.getLogger('ib_insync.event').setLevel(logging.WARNING)
    logging.getLogger('ib_insync.util').setLevel(logging.WARNING)
    logging.getLogger('ib_insync.objects').setLevel(logging.WARNING)
    logging.getLogger('ib_insync.contract').setLevel(logging.WARNING)
    logging.getLogger('ib_insync.order').setLevel(logging.WARNING)
    logging.getLogger('ib_insync.ib').setLevel(logging.WARNING)
    
    # Suppress related lower-level modules used by ib_insync
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    logging.getLogger('eventkit').setLevel(logging.WARNING)
    
# Call to suppress IB logs
suppress_ib_logs()


class IBConnection:
    """
    Class for managing connection to Interactive Brokers
    """
    def __init__(self, host='127.0.0.1', port=7497, client_id=1, timeout=20, readonly=True):
        """
        Initialize the IB connection
        
        Args:
            host (str): TWS/IB Gateway host (default: 127.0.0.1)
            port (int): TWS/IB Gateway port (default: 7497 for paper trading, 7496 for live)
            client_id (int): Client ID for TWS/IB Gateway
            timeout (int): Connection timeout in seconds
            readonly (bool): Whether to connect in readonly mode
        """
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout = timeout
        self.readonly = False
        self.ib = IB()
        self._connected = False
        
        # Suppress ib_insync logs when initializing
        suppress_ib_logs()
    
    def _ensure_event_loop(self):
        """
        Ensure that an event loop exists for the current thread
        """
        try:
            # Check if an event loop exists and is running
            loop = asyncio.get_event_loop()
            if not loop.is_running():
                pass  # Loop exists but not running, which is fine
        except RuntimeError:
            # No event loop exists in this thread, create one
            logger.debug("Creating new event loop for thread")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return True
    
    def connect(self):
        """
        Connect to TWS/IB Gateway
        
        Returns:
            bool: True if successful, False otherwise
        """
        # Suppress logs during connection attempt
        suppress_ib_logs()
        
        try:
            if self._connected and self.ib.isConnected():
                logger.info(f"Already connected to IB")
                return True
            
            logger.info(f"Connecting to IB at {self.host}:{self.port} with client ID {self.client_id}")
            
            # Ensure event loop exists
            self._ensure_event_loop()
            
            self.ib.clientId = self.client_id
            self.ib.connect(self.host, self.port, clientId=self.client_id, readonly=False, timeout=self.timeout)
            
            self._connected = self.ib.isConnected()
            if self._connected:
                logger.info(f"Successfully connected to IB with client ID {self.client_id}")
                return True
            else:
                logger.error(f"Failed to connect to IB with client ID {self.client_id}")
                return False
        except Exception as e:
            error_msg = str(e)
            if "clientId" in error_msg and "already in use" in error_msg:
                logger.error(f"Connection error: Client ID {self.client_id} is already in use by another application.")
                logger.error("Please try using a different client ID, or close other applications connected to TWS/IB Gateway.")
            elif "There is no current event loop" in error_msg:
                logger.error("Asyncio event loop error detected. This may be due to threading issues.")
                logger.error("Please try running your code in the main thread or configuring asyncio properly.")
            else:
                logger.error(f"Error connecting to IB: {error_msg}")
                # Log more detailed error information for debugging
                logger.error(f"Connection details: host={self.host}, port={self.port}, clientId={self.client_id}, readonly={self.readonly}")
                logger.debug(traceback.format_exc())
            
            self._connected = False
            return False
    
    def disconnect(self):
        """
        Disconnect from Interactive Brokers
        """
        if self._connected:
            self.ib.disconnect()
            self._connected = False
            logger.info("Disconnected from IB")
    
    def is_connected(self):
        """
        Check if connected to Interactive Brokers
        
        Returns:
            bool: True if connected, False otherwise
        """
        return self._connected and self.ib.isConnected()
    
    def get_stock_price(self, symbol):
        """
        Get the current price of a stock
        
        Args:
            symbol (str): Stock symbol
            
        Returns:
            float: Current stock price or None if error
        """
        if not self.is_connected():
            logger.warning("Not connected to IB. Attempting to connect...")
            if not self.connect():
                return None
        
        try:
            # Ensure event loop exists for this thread
            self._ensure_event_loop()
            
            # Determine if market is open and set data type accordingly
            is_market_open = is_market_hours()
            
            if not is_market_open:
                # Use frozen data when market is closed
                logger.info(f"Market is closed. Setting market data type to FROZEN for stock {symbol}")
                self.set_market_data_type(2)  # 2 = Frozen
            else:
                # Use live data when market is open
                logger.info(f"Market is open. Setting market data type to LIVE for stock {symbol}")
                self.set_market_data_type(1)  # 1 = Live
            
            # Create a stock contract
            contract = Contract(symbol=symbol, secType='STK', exchange='SMART', currency='USD')
            
            # Qualify the contract
            qualified_contracts = self.ib.qualifyContracts(contract)
            if not qualified_contracts:
                logger.error(f"Failed to qualify contract for {symbol}")
                return None
            
            qualified_contract = qualified_contracts[0]
            
            # Request market data
            ticker = self.ib.reqMktData(qualified_contract)
            
            # Wait for market data to be received - wait a bit longer for frozen data
            wait_time = 1.0 if not is_market_open else 0.2
            self.ib.sleep(wait_time)
            
            # Get the last price
            last_price = ticker.last if ticker.last else (ticker.close if ticker.close else None)
            bid_price = ticker.bid if ticker.bid else None
            ask_price = ticker.ask if ticker.ask else None
            last_rth_trade = ticker.lastRTHTrade.price if hasattr(ticker, 'lastRTHTrade') and ticker.lastRTHTrade else None
            
            # If no last price is available, check other prices
            if last_price is None:
                if bid_price and ask_price:
                    # Use midpoint of bid-ask spread
                    last_price = (bid_price + ask_price) / 2
                elif bid_price:
                    last_price = bid_price
                elif ask_price:
                    last_price = ask_price
                elif last_rth_trade:
                    last_price = last_rth_trade
            
            # Cancel the market data subscription
            self.ib.cancelMktData(qualified_contract)
            
            if last_price is None:
                logger.error(f"Could not get price for {symbol}")
                return None
                
            return last_price
            
        except Exception as e:
            error_msg = str(e)
            if "There is no current event loop" in error_msg:
                logger.error("Asyncio event loop error in get_stock_price. Retrying with new event loop.")
                # Try one more time with a fresh event loop
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    return self.get_stock_price(symbol)
                except Exception as retry_error:
                    logger.error(f"Failed to get stock price after event loop retry: {str(retry_error)}")
                    return None
            else:
                logger.error(f"Error getting {symbol} price: {error_msg}")
            return None
  
    def set_market_data_type(self, data_type=1):
        """
        Set market data type for IB client
        
        Args:
            data_type (int): Market data type
                1 = Live
                2 = Frozen
                3 = Delayed
                4 = Delayed frozen
        
        Returns:
            bool: Success or failure
        """
        try:
            if not self.is_connected():
                logger.warning("Cannot set market data type - not connected")
                return False
                
            logger.info(f"Setting market data type to {data_type}")
            self.ib.reqMarketDataType(data_type)
            return True
        except Exception as e:
            logger.error(f"Error setting market data type: {e}")
            return False
            
    def get_option_chain(self, symbol, expiration=None, right='C', target_strike=None, exchange='SMART'):
        """
        Get option chain for a given symbol, expiration, and right
        
        Args:
            symbol (str): Stock symbol
            expiration (str, optional): Option expiration date in YYYYMMDD format
            right (str, optional): Option right - 'C' for calls, 'P' for puts
            target_strike (float, optional): Specific strike price to look for
            exchange (str, optional): Exchange to use
            
        Returns:
            dict: Option chain data or None if error
        """
        try:
            if not self.is_connected():
                logger.error(f"Cannot get option chain for {symbol} - not connected")
                return None
            
            # Determine if market is open and set data type accordingly
            is_market_open = is_market_hours()
            
            if not is_market_open:
                # Use frozen data when market is closed
                logger.info(f"Market is closed. Setting market data type to FROZEN for {symbol}")
                self.set_market_data_type(2)  # 2 = Frozen
            else:
                # Use live data when market is open
                logger.info(f"Market is open. Setting market data type to LIVE for {symbol}")
                self.set_market_data_type(1)  # 1 = Live
            
            # Rest of the method remains the same...
            stock = Stock(symbol, exchange, 'USD')
            self.ib.qualifyContracts(stock)
            
            # Get stock price for reference
            ticker = self.ib.reqMktData(stock)
            self.ib.sleep(1)  # Wait for data
            
            stock_price = ticker.marketPrice()
            if not stock_price or stock_price <= 0:
                stock_price = ticker.last if hasattr(ticker, 'last') and ticker.last > 0 else None
            if not stock_price or stock_price <= 0:
                stock_price = ticker.close if hasattr(ticker, 'close') and ticker.close > 0 else None
            
            logger.info(f"Current price for {symbol}: {stock_price}")
            
            if not stock_price or stock_price <= 0:
                logger.warning(f"Could not get valid price for {symbol}")
                return None
            
            # Cancel the market data request
            self.ib.cancelMktData(stock)
            
            # Get option chains to find expirations and strikes
            chains = self.ib.reqSecDefOptParams(stock.symbol, '', stock.secType, stock.conId)
            
            if not chains:
                logger.error(f"No option chains found for {symbol}")
                return None
                
            # Get the first exchange's data
            chain = next((c for c in chains if c.exchange == exchange), chains[0])
            
            # If expiration not provided, get the next standard expiration
            if not expiration:
                # Find closest expiration to current date
                if chain.expirations:
                    today = datetime.now().strftime('%Y%m%d')
                    valid_expirations = [exp for exp in chain.expirations if exp >= today]
                    
                    if valid_expirations:
                        expiration = sorted(valid_expirations)[0]
                        logger.info(f"Using expiration {expiration} for {symbol}")
                    else:
                        logger.error(f"No valid expirations found for {symbol}")
                        return None
                else:
                    logger.error(f"No expirations found for {symbol}")
                    return None
            
            if not chain:
                logger.error(f"No option chain found for {symbol} on exchange {exchange}")
                return None
            
            # Get strikes from the chain
            strikes = chain.strikes if hasattr(chain, 'strikes') and chain.strikes else []
            
            # If no strikes available but target_strike provided, use that
            if not strikes and target_strike is not None:
                logger.warning(f"No strikes available for {symbol}, using provided target strike: {target_strike}")
                strikes = [target_strike]
            # If no strikes available and no target_strike, return error
            elif not strikes:
                logger.error(f"No strikes available for {symbol} and no target strike provided")
                return None
                
            # If target_strike is provided, find the closest strike
            if target_strike is not None and strikes:
                logger.info(f"Finding strike closest to {target_strike} for {symbol}")
                closest_strike = min(strikes, key=lambda s: abs(s - target_strike))
                logger.info(f"Selected strike {closest_strike} (from {len(strikes)} available strikes)")
                strikes = [closest_strike]
            
            # Final check to ensure expiration is set
            if not expiration:
                logger.error(f"No expiration date available for {symbol}")
                return None
                
            # Create option contract for each strike
            option_contracts = []
            
            for strike in strikes:
                contract = Option(symbol=symbol, lastTradeDateOrContractMonth=expiration, strike=strike, right=right, exchange=exchange, currency='USD')
                option_contracts.append(contract)
            
            if not option_contracts:
                logger.error(f"No option contracts created for {symbol}")
                return None
            # Get additional data for these contracts
            result = {
                'symbol': symbol,
                'expiration': expiration,  # Just use the first one since we're filtering
                'stock_price': stock_price,
                'right': right,
                'options': []
            }
            
            # Rest of the method remains the same...
            # Qualify and request market data for each option
            for contract in option_contracts:
                try:
                    # Qualify the contract
                    qualified_contracts = self.ib.qualifyContracts(contract)
                    if not qualified_contracts:
                        logger.warning(f"Could not qualify option contract: {contract.symbol} {contract.lastTradeDateOrContractMonth} {contract.strike} {contract.right}")
                        continue
                    
                    qualified_contract = qualified_contracts[0]
                    
                    # Request market data with model computation
                    ticker = self.ib.reqMktData(qualified_contract, '', True, False)  # Add genericTickList='Greeks' to get Greeks
                   
                    # Wait for data to arrive - give more time for Greeks
                    for _ in range(50):
                        self.ib.sleep(0.1)
                        if ticker.modelGreeks is not None and ticker.ask > 0:
                            break
                    
                    # Extract market data
                    bid = ticker.bid if hasattr(ticker, 'bid') and ticker.bid is not None and ticker.bid > 0 else 0
                    ask = ticker.ask if hasattr(ticker, 'ask') and ticker.ask is not None and ticker.ask > 0 else 0
                    last = ticker.last if hasattr(ticker, 'last') and ticker.last is not None and ticker.last > 0 else 0
                    volume = ticker.volume if hasattr(ticker, 'volume') and ticker.volume is not None else 0
                    open_interest = ticker.openInterest if hasattr(ticker, 'openInterest') and ticker.openInterest is not None else 0
                    implied_vol = ticker.impliedVolatility if hasattr(ticker, 'impliedVolatility') and ticker.impliedVolatility is not None else 0
                    
                    # Get real delta from model greeks if available
                    delta = None
                    gamma = None
                    theta = None
                    vega = None
                    
                    if hasattr(ticker, 'modelGreeks') and ticker.modelGreeks:
                        delta = ticker.modelGreeks.delta if hasattr(ticker.modelGreeks, 'delta') else None
                        gamma = ticker.modelGreeks.gamma if hasattr(ticker.modelGreeks, 'gamma') else None
                        theta = ticker.modelGreeks.theta if hasattr(ticker.modelGreeks, 'theta') else None
                        vega = ticker.modelGreeks.vega if hasattr(ticker.modelGreeks, 'vega') else None
                        
                        logger.debug(f"Got real greeks for {contract.symbol} {contract.right} {contract.strike}: delta={delta}, gamma={gamma}, theta={theta}, vega={vega}")
                    else:
                        logger.debug(f"No model greeks available for {contract.symbol} {contract.right} {contract.strike}")
                        
                    # Create option data dictionary
                    option_data = {
                        'strike': contract.strike,
                        'expiration': contract.lastTradeDateOrContractMonth,
                        'option_type': 'CALL' if contract.right == 'C' else 'PUT',
                        'bid': bid,
                        'ask': ask,
                        'last': last,
                        'volume': volume,
                        'open_interest': open_interest,
                        'implied_volatility': implied_vol,
                        'delta': round(delta, 3) if delta is not None else None,
                        'gamma': round(gamma, 5) if gamma is not None else None,
                        'theta': round(theta, 5) if theta is not None else None,
                        'vega': round(vega, 5) if vega is not None else None
                    }
                    
                    # Add to the result
                    result['options'].append(option_data)
                    
                    # Cancel market data request
                    self.ib.cancelMktData(qualified_contract)
        
                except Exception as e:
                    logger.error(f"Error getting market data for option {contract.symbol} {contract.lastTradeDateOrContractMonth} {contract.strike} {contract.right}: {e}")
                    logger.error(traceback.format_exc())
            
            # Sort options by strike price
            result['options'] = sorted(result['options'], key=lambda x: x['strike'])
            
            return result
        except Exception as e:
            logger.error(f"Error retrieving option chain for {symbol}: {e}")
            logger.error(traceback.format_exc())
            return None
    
    def get_portfolio(self):
        """
        Get current portfolio positions and account information from IB
        Returns all positions (Stocks, Options, and other security types)
        
        Returns:
            dict: Dictionary containing account information and all positions
            
        Raises:
            ConnectionError: If connection fails during market hours
            ValueError: If no data available during market hours
        """
        is_market_open = is_market_hours()
        
        if not self.is_connected():
            if is_market_open:
                logger.error("Not connected to IB during market hours")
                raise ConnectionError("Not connected to IB during market hours")
            else:
                # Try to connect even when market is closed
                logger.info("Market is closed, but trying to connect for frozen data")
                if not self.connect():
                    logger.info("Could not connect to IB during closed market. Using mock portfolio data.")
                    return self._generate_mock_portfolio()
        
        try:
            # Set market data type based on market hours
            if not is_market_open:
                # Use frozen data when market is closed
                logger.info("Market is closed. Setting market data type to FROZEN for portfolio")
                self.set_market_data_type(2)  # 2 = Frozen
            else:
                # Use live data when market is open
                logger.info("Market is open. Setting market data type to LIVE for portfolio")
                self.set_market_data_type(1)  # 1 = Live
                
            # Get account summary
            account_id = self.ib.managedAccounts()[0]
            account_values = self.ib.accountSummary(account_id)
            
            if not account_values:
                logger.warning("No account data available")
                # If we don't get account data, use mock portfolio data
                logger.info("Using mock portfolio data as fallback")
                return self._generate_mock_portfolio()
            
            # Extract relevant account information
            account_info = {
                'account_id': account_id,
                'available_cash': 0,
                'account_value': 0
            }
            
            for av in account_values:
                if av.tag == 'TotalCashValue':
                    account_info['available_cash'] = float(av.value)
                elif av.tag == 'NetLiquidation':
                    account_info['account_value'] = float(av.value)
            
            # Get positions
            portfolio = self.ib.portfolio()
            positions = {}
            
            # Process all positions (both Stocks and Options)
            stock_count = 0
            option_count = 0
            other_count = 0
            
            # Import Option class for isinstance check
            from ib_insync import Option
            
            for position in portfolio:
                try:
                    symbol = position.contract.symbol
                    position_key = symbol
                    position_type = 'UNKNOWN'
                    
                    # Determine position type and create an appropriate key
                    if isinstance(position.contract, Stock):
                        position_type = 'STK'
                        stock_count += 1
                    elif isinstance(position.contract, Option):
                        position_type = 'OPT'
                        option_count += 1
                        # For options, create a unique key including strike, expiry, and right
                        expiry = position.contract.lastTradeDateOrContractMonth
                        strike = position.contract.strike
                        right = position.contract.right
                        position_key = f"{symbol}_{expiry}_{strike}_{right}"
                    else:
                        position_type = position.contract.secType
                        other_count += 1
                    
                    # Store position data
                    positions[position_key] = {
                        'shares': position.position,
                        'avg_cost': position.averageCost,
                        'market_price': position.marketPrice,
                        'market_value': position.marketValue,
                        'unrealized_pnl': position.unrealizedPNL,
                        'realized_pnl': position.realizedPNL,
                        'contract': position.contract,
                        'security_type': position_type
                    }
                    
                except Exception as e:
                    logger.error(f"Error processing position: {str(e)}")
            
            if is_market_open:
                logger.info(f"Processed {stock_count} stock positions, {option_count} option positions, and {other_count} other positions")
            else:
                logger.info(f"Processed {stock_count} stock positions, {option_count} option positions, and {other_count} other positions using FROZEN data")
            
            if not positions:
                # If we don't get any positions, use mock portfolio data
                logger.info("No positions found. Using mock portfolio data as fallback.")
                return self._generate_mock_portfolio()
            
            return {
                'account_id': account_id,
                'available_cash': account_info.get('available_cash', 0),
                'account_value': account_info.get('account_value', 0),
                'positions': positions,
                'is_frozen': not is_market_open  # Indicate if data is frozen
            }
                
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error getting portfolio: {error_msg}")
            logger.error(traceback.format_exc())
            
            # Only use mock data if not during market hours
            if not is_market_open:
                logger.info("Market is closed. Using mock portfolio data due to error.")
                return self._generate_mock_portfolio()
            else:
                # During market hours, propagate the error
                raise
    
    def _generate_mock_portfolio(self):
        """
        Generate a mock portfolio with stock and option positions and $1M cash
        
        Returns:
            dict: Mock portfolio data
        """
        # Use our NVDA mock price
        nvda_price = 905.75
        nvda_position = 5000
        nvda_value = nvda_price * nvda_position
        
        # Create mock account data
        account_id = "U1234567"  # Mock account number
        total_cash = 1000000.00  # $1M cash as requested
        
        # Create a contract for NVDA stock position
        from ib_insync import Contract, Option
        nvda_contract = Contract(
            symbol="NVDA",
            secType="STK",
            exchange="SMART",
            currency="USD"
        )
        
        # Create an option contract for a short NVDA put
        from datetime import datetime, timedelta
        # Calculate this week's Friday
        today = datetime.now()
        days_until_friday = (4 - today.weekday()) % 7
        # If today is Friday, use today. Otherwise calculate the upcoming Friday
        this_friday = today if days_until_friday == 0 else today + timedelta(days=days_until_friday)
        this_friday_str = this_friday.strftime('%Y%m%d')
        
        # Premium value is per contract
        nvda_put_price = 15.50
        nvda_put_strike = 850.0
        nvda_put_quantity = -10  # Short 10 contracts
        nvda_put_value = nvda_put_price * 100 * abs(nvda_put_quantity)  # Market value calculation
        
        nvda_put_contract = Option(
            symbol="NVDA",
            lastTradeDateOrContractMonth=this_friday_str,
            strike=nvda_put_strike,
            right='P',
            exchange='SMART',
            currency='USD'
        )
        
        # Create an option contract for a short NVDA call
        # Premium value is per contract
        nvda_call_price = 12.75
        nvda_call_strike = 950.0
        nvda_call_quantity = -5  # Short 5 contracts
        nvda_call_value = nvda_call_price * 100 * abs(nvda_call_quantity)  # Market value calculation
        
        nvda_call_contract = Option(
            symbol="NVDA",
            lastTradeDateOrContractMonth=this_friday_str,
            strike=nvda_call_strike,
            right='C',
            exchange='SMART',
            currency='USD'
        )
        
        # Calculate total value including options premium
        total_value = total_cash + nvda_value + nvda_put_value + nvda_call_value
        
        # Create the portfolio object with both stock and option positions
        positions = {
            "NVDA": {
                'shares': nvda_position,
                'avg_cost': nvda_price * 0.8,  # Assume we bought it 20% cheaper
                'market_price': nvda_price,
                'market_value': nvda_value,
                'unrealized_pnl': nvda_value - (nvda_price * 0.8 * nvda_position),
                'realized_pnl': 0,
                'contract': nvda_contract,
                'security_type': 'STK'
            },
            f"NVDA_{this_friday_str}_{nvda_put_strike}_P": {
                'shares': nvda_put_quantity,
                'avg_cost': nvda_put_price,
                'market_price': nvda_put_price,
                'market_value': -nvda_put_value,  # Negative for short positions
                'unrealized_pnl': 0,
                'realized_pnl': 0,
                'contract': nvda_put_contract,
                'security_type': 'OPT'
            },
            f"NVDA_{this_friday_str}_{nvda_call_strike}_C": {
                'shares': nvda_call_quantity,
                'avg_cost': nvda_call_price,
                'market_price': nvda_call_price,
                'market_value': -nvda_call_value,  # Negative for short positions
                'unrealized_pnl': 0,
                'realized_pnl': 0,
                'contract': nvda_call_contract,
                'security_type': 'OPT'
            }
        }
        
        logger.warning("Using MOCK PORTFOLIO DATA - showing NVDA shares and option positions with $1M cash")
        
        return {
            'account_id': account_id,
            'available_cash': total_cash,
            'account_value': total_value,
            'positions': positions,
            'is_mock': True
        }

    def create_option_contract(self, symbol, expiry, strike, option_type, exchange='SMART', currency='USD'):
        """
        Create an option contract for TWS
        
        Args:
            symbol (str): Ticker symbol
            expiry (str): Expiration date in YYYYMMDD format
            strike (float): Strike price
            option_type (str): 'C', 'CALL', 'P', or 'PUT'
            exchange (str): Exchange name, default 'SMART'
            currency (str): Currency code, default 'USD'
            
        Returns:
            Option: Contract object ready for use with TWS
        """
        # Normalize option type to standard format
        if option_type.upper() in ['C', 'CALL']:
            right = 'C'
        elif option_type.upper() in ['P', 'PUT']:
            right = 'P'
        else:
            logger.error(f"Invalid option type: {option_type}")
            return None
            
        try:
            contract = Option(
                symbol=symbol, 
                lastTradeDateOrContractMonth=expiry,
                strike=float(strike),
                right=right,
                exchange=exchange,
                currency=currency
            )
            
            logger.info(f"Created option contract: {symbol} {expiry} {strike} {right}")
            return contract
        except Exception as e:
            logger.error(f"Error creating option contract: {str(e)}")
            logger.error(traceback.format_exc())
            return None
            
    def create_order(self, action, quantity, order_type='LMT', limit_price=None, tif='DAY'):
        """
        Create an order for TWS
        
        Args:
            action (str): 'BUY' or 'SELL'
            quantity (int): Number of contracts
            order_type (str): 'MKT', 'LMT', etc.
            limit_price (float): Price for limit orders
            tif (str): Time in force - 'DAY', 'GTC', etc.
            
        Returns:
            Order: Order object ready for use with TWS
        """
        from ib_insync import LimitOrder, MarketOrder
        
        try:
            if order_type.upper() == 'LMT':
                if limit_price is None:
                    logger.error("Limit price required for limit orders")
                    return None
                    
                order = LimitOrder(
                    action=action.upper(),
                    totalQuantity=quantity,
                    lmtPrice=limit_price,
                    tif=tif
                )
            elif order_type.upper() == 'MKT':
                order = MarketOrder(
                    action=action.upper(),
                    totalQuantity=quantity,
                    tif=tif
                )
            else:
                logger.error(f"Unsupported order type: {order_type}")
                return None
                
            logger.info(f"Created {order_type} order: {action} {quantity} contracts")
            return order
        except Exception as e:
            logger.error(f"Error creating order: {str(e)}")
            logger.error(traceback.format_exc())
            return None
            
    def place_order(self, contract, order):
        """
        Place an order for a contract
        
        Args:
            contract: The contract to trade
            order: The order to place
            
        Returns:
            dict: Result with order details
        """
        if not self.is_connected():
            logger.error("Cannot place order - not connected to TWS")
            return None
            
        try:   
            # Place the order
            trade = self.ib.placeOrder(contract, order)
            
            # Wait for order acknowledgment (order ID assigned)
            timeout = 3  # seconds
            start_time = time.time()
            
            # Check if we have a valid trade object with orderStatus
            if not hasattr(trade, 'orderStatus'):
                logger.warning("No orderStatus in trade object, returning basic order data")
                # Create a basic result with just the order ID
                return {
                    'order_id': getattr(order, 'orderId', 0),
                    'status': 'Submitted',
                    'filled': 0,
                    'remaining': getattr(order, 'totalQuantity', 0),
                    'avg_fill_price': 0,
                    'is_mock': False
                }
                
            # Wait for order ID to be assigned
            while not trade.orderStatus.orderId and time.time() - start_time < timeout:
                self.ib.waitOnUpdate(timeout=0.1)
                
            # Create result dictionary with safe attribute access
            order_status = {
                'order_id': getattr(trade.orderStatus, 'orderId', 0),
                'status': getattr(trade.orderStatus, 'status', 'Submitted'),
                'filled': getattr(trade.orderStatus, 'filled', 0),
                'remaining': getattr(trade.orderStatus, 'remaining', getattr(order, 'totalQuantity', 0)),
                'avg_fill_price': getattr(trade.orderStatus, 'avgFillPrice', 0),
                'perm_id': getattr(trade.orderStatus, 'permId', 0),
                'last_fill_price': getattr(trade.orderStatus, 'lastFillPrice', 0),
                'client_id': getattr(trade.orderStatus, 'clientId', 0),
                'why_held': getattr(trade.orderStatus, 'whyHeld', ''),
                'market_cap': getattr(trade.orderStatus, 'mktCapPrice', 0),
                'is_mock': False
            }
            
            logger.info(f"Order placed: {order_status}")
            return order_status
        except Exception as e:
            logger.error(f"Error placing order: {str(e)}")
            logger.error(traceback.format_exc())
            # If we at least have an order ID, return that with an error status
            try:
                order_id = getattr(order, 'orderId', 0)
                if order_id > 0:
                    return {
                        'order_id': order_id, 
                        'status': 'Error',
                        'filled': 0,
                        'remaining': getattr(order, 'totalQuantity', 0),
                        'error': str(e),
                        'is_mock': False
                    }
            except:
                pass
            
            return None

    def check_order_status(self, order_id):
        """
        Check the status of an order by its IB order ID
        
        Args:
            order_id (int): The IB order ID to check
            
        Returns:
            dict: Order status information or None if error
        """
        logger.info(f"Checking status for order with IB ID: {order_id}")
        
        try:
            # Ensure connection
            if not self.is_connected():
                logger.error("Not connected to TWS")
                return None
            
            # Ensure order ID is an integer
            order_id = int(order_id)
            
            # Get all open orders
            open_orders = self.ib.openOrders()
            print(f"open_orders: {open_orders}")
            
            # Check if order is in open orders
            for o in open_orders:
                if hasattr(o, 'orderId') and o.orderId == order_id:
                    # Check if it's a contract+order tuple or an order with status
                    if hasattr(o, 'orderStatus'):
                        logger.info(f"Found open order with ID {order_id}, status: {o.orderStatus.status}")
                        return {
                            'status': o.orderStatus.status,
                            'filled': o.orderStatus.filled,
                            'remaining': o.orderStatus.remaining,
                            'avg_fill_price': float(o.orderStatus.avgFillPrice or 0),
                            'last_fill_price': float(o.orderStatus.lastFillPrice or 0),
                            'commission': float(o.orderStatus.commission or 0),
                            'why_held': o.orderStatus.whyHeld
                        }
                    else:
                        # This might be just the order object without status
                        logger.info(f"Found open order with ID {order_id}, but no status information")
                        return {
                            'status': 'Submitted',  # Default status for found orders
                            'filled': 0,
                            'remaining': o.totalQuantity if hasattr(o, 'totalQuantity') else 0,
                            'avg_fill_price': 0,
                            'last_fill_price': 0,
                            'commission': 0,
                            'why_held': ''
                        }
            
            # Check trades for this order ID
            trades = self.ib.trades()
            for trade in trades:
                if hasattr(trade.order, 'orderId') and trade.order.orderId == order_id:
                    logger.info(f"Found trade with order ID {order_id}, status: {trade.orderStatus.status}")
                    return {
                        'status': trade.orderStatus.status,
                        'filled': trade.orderStatus.filled,
                        'remaining': trade.orderStatus.remaining,
                        'avg_fill_price': float(trade.orderStatus.avgFillPrice or 0),
                        'last_fill_price': float(trade.orderStatus.lastFillPrice or 0),
                        'commission': float(trade.orderStatus.commission or 0),
                        'why_held': trade.orderStatus.whyHeld
                    }
            
            # Check execution history if not found in open orders or trades
            executions = self.ib.executions()
            for execution in executions:
                if execution.orderId == order_id:
                    logger.info(f"Found completed order with ID {order_id}")
                    # Get commission info from commissions report
                    commission = 0
                    for fill in self.ib.fills():
                        if fill.execution.orderId == order_id:
                            commission += float(fill.commissionReport.commission or 0)
                    
                    # Map to our standard format
                    return {
                        'status': 'Filled',
                        'filled': execution.shares,
                        'remaining': 0,
                        'avg_fill_price': float(execution.price or 0),
                        'commission': commission
                    }
            
            # Order not found
            logger.warning(f"Order with ID {order_id} not found")
            return {
                'status': 'NotFound',
                'filled': 0,
                'remaining': 0,
                'avg_fill_price': 0
            }
            
        except Exception as e:
            logger.error(f"Error checking order status: {str(e)}")
            logger.error(traceback.format_exc())
            return None
        
    def cancel_order(self, order_id):
        """
        Cancel an open order by its IB order ID
        
        Args:
            order_id (int): The IB order ID to cancel
            
        Returns:
            dict: Result with success/failure info
        """
        logger.info(f"Cancelling order with IB ID: {order_id}")
        
        try:
            # Ensure connection
            if not self.is_connected():
                logger.error("Not connected to TWS")
                return {'success': False, 'error': 'Not connected to TWS'}
            
            # Ensure order ID is an integer
            order_id = int(order_id)
            
            # Get all open orders
            open_orders = self.ib.openOrders()
            
            # Find the order to cancel
            order_to_cancel = None
            for o in open_orders:
                if hasattr(o, 'orderId') and o.orderId == order_id:
                    order_to_cancel = o
                    break
            
            # If not found in open orders, check trades
            if not order_to_cancel:
                trades = self.ib.trades()
                for trade in trades:
                    if hasattr(trade.order, 'orderId') and trade.order.orderId == order_id:
                        order_to_cancel = trade.order
                        break
            
            if not order_to_cancel:
                logger.warning(f"Order with ID {order_id} not found in open orders or trades")
                return {'success': False, 'error': f"Order with ID {order_id} not found in open orders"}
            
            # Cancel the order
            logger.info(f"Cancelling order: {order_to_cancel}")
            try:
                self.ib.cancelOrder(order_to_cancel)
                logger.info(f"Cancellation request sent for order {order_id}")
                return {'success': True, 'message': f"Cancellation request sent for order {order_id}"}
            except Exception as e:
                logger.error(f"Error cancelling order: {str(e)}")
                return {'success': False, 'error': str(e)}
            
        except Exception as e:
            logger.error(f"Error in cancel_order: {str(e)}")
            logger.error(traceback.format_exc())
            return {'success': False, 'error': str(e)}