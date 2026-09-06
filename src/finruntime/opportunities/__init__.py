"""BTC opportunity controller using the existing FIN paper execution contracts.

Research/paper only. Portfolio equity excludes all calibration accounts.
"""
STRATEGY_ID = 'btc_opportunity_paper_v1'
FAMILIES = ('daily_trend', 'trend_pullback', 'range_rebound', 'breakout')
VERSION = 'btc-opportunity-v1'
