import unittest
from unittest.mock import patch, Mock
from pathlib import Path
import numpy as np
import pandas as pd
from market import get_candles
from strategy import enrich, backtest
from streamlit.testing.v1 import AppTest


def fixture():
    t=pd.date_range('2026-11-02 09:15', periods=70, freq='5min',tz='Asia/Kolkata')
    c=24000+np.sin(np.arange(70)/5)*50+np.arange(70)
    return pd.DataFrame(dict(timestamp=t,open=c,high=c+10,low=c-10,close=c+2,volume=np.ones(70)*100))


class DashboardTests(unittest.TestCase):
    def test_market_get_only_and_parse(self):
        rows=[['2026-11-02T09:15:00+0530',100,110,90,105,1000,0]]
        with patch('requests.get',return_value=Mock(status_code=200,json=lambda:{'data':{'candles':rows}})) as req:
            x=get_candles('test-key','nse_cm|test','15min',14)
            self.assertEqual(len(x),1)
            self.assertEqual(str(x.timestamp.dt.tz),'Asia/Kolkata')
            self.assertFalse(req.call_args.kwargs['allow_redirects'])
            self.assertEqual(req.call_args.args[0],'https://mis.kotaksecurities.com/market-data/1.0/historical/details')

    def test_error_does_not_expose_key(self):
        with patch('requests.get',side_effect=RuntimeError('test-secret')):
            with self.assertRaises(RuntimeError) as e: get_candles('test-secret','nse_cm|test','5min',14)
            self.assertNotIn('test-secret',str(e.exception))

    def test_api_limits(self):
        for interval,days in [('1hour',14),('5min',60)]:
            with self.assertRaises(ValueError): get_candles('test','nse_cm|test',interval,days)

    def test_indicators_no_future_leak(self):
        x=fixture(); full=enrich(x); partial=enrich(x.iloc[:40])
        pd.testing.assert_frame_equal(full.iloc[:40].reset_index(drop=True),partial)
        self.assertTrue(np.isfinite(full.atr).all())
        x.volume=0
        self.assertTrue((enrich(x).signal=='WAIT').all())

    def test_stop_first_and_gap_loss(self):
        x=fixture().iloc[:5].copy(); x['signal']='WAIT'; x['atr']=10.
        x.loc[1,'signal']='BUY'; x.loc[2,['open','high','low','close']]=[100,140,80,110]
        r=backtest(x)
        self.assertEqual(r.iloc[0].outcome,'STOP'); self.assertEqual(r.iloc[0].R,-1)
        x.loc[2,['open','high','low','close']]=[100,110,95,105]
        x.loc[3,['open','high','low','close']]=[70,75,65,70]
        self.assertEqual(backtest(x).iloc[0].R,-2)

    def test_no_overnight_entry(self):
        x=fixture().iloc[:4].copy();x['signal']='WAIT';x['atr']=10.
        x.loc[2,'signal']='BUY';x.loc[3,'timestamp']+=pd.Timedelta(days=1)
        self.assertTrue(backtest(x).empty)

    def test_ui_and_paper_journal(self):
        app=AppTest.from_file(str(Path('app.py').resolve())).run()
        self.assertFalse(app.exception)
        app.session_state['candles']=fixture()
        app.session_state['request']=('nse_cm|Nifty 50','15min',14)
        app.session_state['fetched_at']='synthetic test'
        app.run();self.assertFalse(app.exception)
        next(b for b in app.button if b.label=='Record paper trade').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.session_state['journal']),1)
        self.assertEqual(app.session_state['journal'][0]['points'],0)

    def test_no_order_capability_in_app(self):
        import ast
        for name in ['app.py','market.py','strategy.py']:
            tree=ast.parse(Path(name).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                    self.assertNotIn(node.func.attr,{'post','put','delete','place_order','modify_order','cancel_order','totp_login','totp_validate'})
        self.assertNotIn('kotakneoapi',Path('requirements.txt').read_text())

if __name__=='__main__': unittest.main()
