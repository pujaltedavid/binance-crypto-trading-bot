import websocket
import requests
import time
import talib
import random
import json
import pprint
from variables import streams5m, cryptos
import numpy as np


def average(x, n):
    """ Return the average of last n samples of x. """
    return sum(x[-n:])/n


def movingAverage(x, n):
    """ Calculate the average of x in [0:n+1, 1:n+2, ..., l-n:l+1] """
    l = len(x)
    return [average(x[:n+i+1], n) for i in range(l-n)]


def EMA(x, n, smoothing=2):
    """ Calculate the exponential moving average of x in
    [0:n+1, 1:n+2, ..., l-n:l+1].
    
    The first EMA is a SMA (Simple Moving Average). """
    
    ema = [average(x[:n], n)] # The first sample is a SMA.
    
    p = smoothing / (n+1) # Rho, weight factor.
    
    for price in x[n:]:
        ema.append(price*p + ema[-1]*(1-p))
    
    return ema
    

def updateEMA(actual, previous, n, smoothing=2):
    """ Return new value of EMA using the previous EMA sample. """
    global ema

    p = smoothing / (n+1) # Rho, weight factor.
    return actual*p + previous*(1-p)


def relativeStrengthIndicator(x, period, n=1):
    """Calculates the rsi-period of the last n samples."""
    # may be inefficient and does not match with binance rsi
    # Pass to talib the last n+period samples and remove NaN from the result.
    return talib.RSI(np.array(x[-(n+period):]), period)[period:]


def diff(x, N=1):
    """Differentiate the array by n samples"""
    return [x[n]-x[n-N] for n in range(N, len(x))]


def wait():
    """Make sure that there is sufficient time to do things before a 5 minute
    candle closes.
    (Wait till minute 6 or 1 if it is 5, 4 or 0, 9)
    """
    i = 0
    while time.gmtime()[4] % 5 in {0, 4}:
        print('Waiting until getting the requests' + '.'*(i % 4) + ' '*(4-i % 4),
              end='\r')
        i += 1
        time.sleep(1)


def init_wallet():
    global wallet
    for crypto in cryptos:
        wallet[crypto] = 0

    wallet['USDT'] = INIT_USDT
    wallet['BNBFORFEE'] = INIT_BNB


def printThresholds():
    print('CALCULATED THRESHOLDS:')
    print('CRYPTO', ' '*14, 'SELL LIMIT', ' '*10, 'STOP LOSS')
    print('-'*53)
    for crypto in cryptos:
        sellLimit, stopLoss = (str(round(x, 5) if x else None)
                               for x in thresholds[crypto])

        print(crypto, ' '*(20-len(crypto)), sellLimit,
              ' '*(20-len(sellLimit)), stopLoss)
    print('! Calculated Thresholds are not used in this version.')


def calculateThresholds(crypto, verbose=False):
    """ Calculate sell limit and stop loss based on the previous 24h.

    returns:
        (sell_limit, stop_loss) all in percentage (1 -> x1 -> 100%)
        sell_limit is None if sell limit is less than x1.005
    """
    print('\n\nCalculate Thresholds of', crypto) if verbose else None
    print(len(closes[crypto])) if verbose else None
    # print(closes[crypto])
    print(len(ma[crypto][50])) if verbose else None
    # print(ma[crypto][50])
    # print(ma[crypto][14])
    print(len(ma[crypto][14])) if verbose else None
    # print(ma[crypto][6])
    print(len(ma[crypto][6])) if verbose else None
    relaxThresholds = 0.8  # Percentage to relax calculated thresholds

    i = 50+40  # First point to have all MAs (and not crash)
    wins, losses = [], []  # Array of percentages up and down

    while i < 498:  # 500 5min candlesticks (latest 41h 40min)
        if isGoingToRise(crypto, usingGlobals=False, lastIndex=i):
            print('\n\n Is going to rise', crypto) if verbose else None
            # Calculate index of the next cross down
            j = i+1
            while j < 498:
                if (ma[crypto][6][j] >= ma[crypto][14][j]
                        and ma[crypto][6][j+1] < ma[crypto][14][j+1]):
                    j += 1  # cross is made on j+1
                    break
                else:
                    j += 1
            else:
                break  # No more crosses, exit the outer loop

            (print(crypto, 'rose on', i, '(',
                   time.gmtime(candles[crypto][i]['t']/1000), ') and fell on',
                   j, '(', time.gmtime(candles[crypto][j]['t']/1000), ')')
             if verbose else None)

            # Get maximum (global) and minimum (on close) value
            # that's because it is interesting to sell a win on any time
            # but sell a loss only on a close.
            actual = closes[crypto][i]
            maximum = actual
            minimum = actual
            for k in range(i+1, j+1):
                if candles[crypto][k]['h'] > maximum:
                    maximum = candles[crypto][k]['h']
                if closes[crypto][k] < minimum:
                    minimum = closes[crypto][k]

            if maximum != actual:
                wins.append(maximum/actual - 1)
            if minimum != actual:
                losses.append(1 - minimum/actual)

            i = j
        else:
            i += 1

    print('len wins:', len(wins)) if verbose else None
    print('len losses:', len(losses)) if verbose else None

    avgWin = sum(wins)/len(wins) if len(wins) > 0 else 0
    avgWin *= relaxThresholds

    avgLoss = sum(losses)/len(losses) if len(losses) > 0 else 0
    avgLoss *= relaxThresholds

    return (avgWin, avgLoss) if avgWin > 0.005 else (None, None)


def get_data(catchUp=False):
    global candles, closes, ma, ema, rsi, thresholds, lastTradeTime

    try:
        for crypto in cryptos:
            print('Catching up' if catchUp else 'Getting request of',
                  crypto, end='\r')

            if catchUp:
                # Find the last correct closed candle
                lastCloseTime = None
                i = 1
                while not lastCloseTime:
                    # If the candle -i is a close candle
                    if candles[crypto][-i]['x']:
                        lastCloseTime = candles[crypto][-i]['T']/1000
                    else:
                        i += 1

                # Get last close real time
                timeNow = time.gmtime()
                extraMin = timeNow.tm_min % 5
                lastCloseRealTime = time.time()-extraMin*60-timeNow.tm_sec

                # Difference in seconds
                diffTime = int(lastCloseRealTime - lastCloseTime)

                # Difference in 5 min candles
                diffCandles = int(round(diffTime/(60*5), 1))

                assert diffTime % (60*5) < 100, 'Misaligned time calculus'

                # +1 because it requests the last non-closed too, which is
                # not needed
                limit = diffCandles+1 if diffCandles != 0 else None

            else:
                limit = 1000

            # Get limit entries, if limit != 0
            if limit:
                url = ('https://api.binance.com/api/v3/klines?symbol='+crypto +
                       '&interval=5m&limit='+str(limit))
                try:
                    # Remove last candle because it may not be closed
                    newCandles = requests.get(url).json()[:-1]

                    # Modify kline structure to match API with WebSocket
                    newCandles = [{
                        't': x[0],
                        'T': x[6],
                        's': crypto,
                        'i': '5m',
                        'f': None,
                        'L': None,
                        'o': x[1],
                        'c': x[4],
                        'h': float(x[2]),
                        'l': x[3],
                        'v': x[5],
                        'n': x[8],
                        'x': True,
                        'q': x[7],
                        'V': x[9],
                        'Q': x[10],
                        'B': x[11]
                    } for x in newCandles]

                    if not catchUp:
                        candles[crypto] = []
                        closes[crypto] = []
                        ma[crypto] = {50: [None]*50, 14: [None]*14,
                                      6: [None]*6}
                        ema[crypto] = {50: [None]*50, 20: [None]*20}
                        rsi[crypto] = []
                        lastTradeTime[crypto] = None

                    candles[crypto].extend(newCandles)
                    closes[crypto].extend([float(x['c']) for x in newCandles])

                    # Calculate moving averages
                    for n in [6, 14, 50]:
                        # Only calculate for the last int(limit)-1 values
                        ma[crypto][n].extend(movingAverage(
                                             closes[crypto][-limit+1:], n))
                        
                    # Calculate exponential moving averages
                    for n in [20, 50]:
                        # Only calculate for the last int(limit)-1 values
                        ema[crypto][n].extend(EMA(
                                              closes[crypto][-limit+1:], n))

                    rsi[crypto].extend(relativeStrengthIndicator(
                        closes[crypto], RSI_PERIOD, limit-1))

                    #thresholds[crypto] = calculateThresholds(crypto,
                    #                                         verbose=True)

                    print('Catching up' if catchUp else 'Getting request of',
                          crypto, ' '*(10-len(crypto)), 'OK')
                except Exception as e:
                    print('Catching up' if catchUp else 'Getting request of',
                          crypto, ' '*(10-len(crypto)), 'ERROR', e)
        print('\n\n\n')
        #printThresholds()
        #updateThresholds()

    except Exception as e:
        print('programming error on get_data:', e)


def getBnbPrice():
    # Get BNB price to calculate exact fees
    url = 'https://api.binance.com/api/v3/avgPrice?symbol=BNBUSDT'
    return float(requests.get(url).json()['price'])


def updateThresholds():
    with open('thresholds.json', 'w') as textFile:
        json.dump(thresholds, textFile)


def updateCandles():
    with open('candles.json', 'w') as textFile:
        json.dump(candles, textFile)
        

def updateTradeHistory(crypto, action, long, price, nCoins, total, fee, time,
                       target=None, stop=None):
    """
    action: True if buy else sell
    long: True if long else False for short
    price in USDT
    fee   in BNB
    total in USDT (nCoins*price)
    """
    #global tradeHistory

    trade = {
        'crypto': crypto,
        'time': time,
        'action': 'Buy' if action else 'Sell',
        'position': ('Long' if long else 'Short') + ' x' + str(LEVERAGE),
        'stop': stop,
        'target': target,
        'price': price,
        'filled': nCoins,
        'fee': fee,
        'total': round(total, 10)  # Avoid weird things like js
    }
    # tradeHistory.append(trade)

    with open('tradeHistory.txt', 'a') as textFile:
        textFile.write(','+json.dumps(trade))


def isGoingToRise(crypto, verbose=False):
    try:
        ema20 = ema[crypto][20][-3]
        ema50 = ema[crypto][50][-3]
        fractalMin = float(candles[crypto][-3]['l'])
        
        print(ema20, '>', fractalMin, '>', ema50, '?') if verbose else None
        
        print(fractalMin, '<', candles[crypto][-5]['l'],
              '?') if verbose else None
        print(fractalMin, '<', candles[crypto][-4]['l'],
              '?') if verbose else None
        print(fractalMin, '<', candles[crypto][-2]['l'],
              '?') if verbose else None
        print(fractalMin, '<', candles[crypto][-1]['l'],
              '?') if verbose else None
           
        
        # Is minimum fractal and the minimum is below the ema20 and above the
        # ema50.
        if (ema20 > fractalMin > ema50 and
           fractalMin < float(candles[crypto][-5]['l']) and
           fractalMin < float(candles[crypto][-4]['l']) and
           fractalMin < float(candles[crypto][-2]['l']) and
           fractalMin < float(candles[crypto][-1]['l'])):
            
            print('Yes') if verbose else None
            return True
   
    except Exception as e:
        print('Programming error on isGoingToRise:', e)
        
    return False


def isGoingToFall(crypto, verbose):
    try:
        ema20 = ema[crypto][20][-3]
        ema50 = ema[crypto][50][-3]
        fractalMax = float(candles[crypto][-3]['h'])
        
        print(ema20, '<', fractalMax, '<', ema50, '?') if verbose else None
        
        print(fractalMax, '>', candles[crypto][-5]['h'],
              '?') if verbose else None
        print(fractalMax, '>', candles[crypto][-4]['h'],
              '?') if verbose else None
        print(fractalMax, '>', candles[crypto][-2]['h'],
              '?') if verbose else None
        print(fractalMax, '>', candles[crypto][-1]['h'],
              '?') if verbose else None
           
        
        # Is maximum fractal and the maximum is above the ema20 and below the
        # ema50.
        if (ema20 < fractalMax < ema50 and
           fractalMax > float(candles[crypto][-5]['h']) and
           fractalMax > float(candles[crypto][-4]['h']) and
           fractalMax > float(candles[crypto][-2]['h']) and
           fractalMax > float(candles[crypto][-1]['h'])):
            
            print('Yes') if verbose else None
            return True
   
    except Exception as e:
        print('Programming error on isGoingToFall:', e)
        
    return False


def buy(crypto, long):
    try:
        global wallet, closes, currentCryptos

        # Get the price to spend
        if (wallet['USDT'] > 200 and currentCryptos < MAX_CURRENT_CRYPTOS and
            (not lastTradeTime[crypto] or
             time.time()-lastTradeTime[crypto] > 3600)):

            print('Bought', 'long ' if long else 'short', 'x'+str(LEVERAGE),
            crypto, ' '*(9-len(crypto)), 'at', closes[crypto][-1])
                  

            usdtSpent = (INIT_USDT/MAX_CURRENT_CRYPTOS
                         if wallet['USDT'] > INIT_USDT/MAX_CURRENT_CRYPTOS
                         else wallet['USDT']/MAX_CURRENT_CRYPTOS)

            wallet['USDT'] -= usdtSpent

            # Buy coins
            buyPrice = closes[crypto][-1]
            nCoins = usdtSpent*LEVERAGE/buyPrice
            wallet[crypto] = [nCoins, buyPrice, long]

            # Fee
            bnbPrice = getBnbPrice()
            fee = (nCoins*buyPrice*MAKERFEERATE/bnbPrice if long else
                   nCoins*buyPrice*TAKERFEERATE/bnbPrice)
            wallet['BNBFORFEE'] -= fee

            updateTradeHistory(crypto, True, long, buyPrice,
                               nCoins, usdtSpent, fee, time.time(),
                               stop=targetStop[crypto][0],
                               target=targetStop[crypto][1])

            currentCryptos += 1

    except Exception as e:
        print('programming error on buy:', e)


def sell(crypto, price):
    # target indicates whether the limit order has been executed
    try:
        global wallet, currentCryptos, lastTradeTime
    
        nCoins = wallet[crypto][0]
        buyPrice = wallet[crypto][1]
    
        # Long position
        if wallet[crypto][2]:
            print('Sold long ', 'x'+str(LEVERAGE), crypto,
                  ' '*(9-len(crypto)), 'at',
                  round(price, 5), '('+str(wallet[crypto][1])+')',
                  ('target' if price >= wallet[crypto][1] else 'stop  '),
                  '('+str(round((price/wallet[crypto][1])*100-100, 3))+'%)')
            
            wallet[crypto] = 0
    
            # Here goes small algebra
            usdtEarnt = nCoins * (price - buyPrice + buyPrice/LEVERAGE)
            wallet['USDT'] += usdtEarnt
    
            # Fee
            bnbPrice = getBnbPrice()
            fee = nCoins*price*TAKERFEERATE/bnbPrice
            wallet['BNBFORFEE'] -= fee
    
            updateTradeHistory(crypto, False, True, price, nCoins, usdtEarnt,
                               fee, time.time())
            
        
        # Short position
        else:
            print('Sold short', 'x'+str(LEVERAGE), crypto,
                  ' '*(9-len(crypto)), 'at',
                  round(price, 5), '('+str(wallet[crypto][1])+')',
                  ('target' if price <= wallet[crypto][1] else 'stop  '),
                  '('+str(round((1 - price/wallet[crypto][1])*100, 3))+'%)')
            
            wallet[crypto] = 0
    
            # Smaller algebra
            usdtEarnt = nCoins * (buyPrice - price + buyPrice/LEVERAGE)
            
            wallet['USDT'] += usdtEarnt
    
            # Fee
            bnbPrice = getBnbPrice()
            fee = nCoins*price*MAKERFEERATE/bnbPrice
            wallet['BNBFORFEE'] -= fee
    
            updateTradeHistory(crypto, False, False, price, nCoins, usdtEarnt,
                               fee, time.time())
            
        lastTradeTime[crypto] = time.time()

        currentCryptos -= 1

    except Exception as e:
        print('programming error on sell:', e)


def init_socket():
    global ws

    # Wait till there is sufficient time between requests and new candlesticks
    wait()

    ws = None

    SOCKET = 'wss://stream.binance.com:9443/stream?streams='+streams5m

    # If 5 seconds passed since last connection response, raise an error.
    websocket.setdefaulttimeout(5)
    ws = websocket.WebSocketApp(SOCKET, on_open=on_open, on_close=on_close,
                                on_message=on_message, on_error=on_error)
    ws.run_forever()


def on_open(ws):
    print('Opened connection.')
    global connectionFailed

    get_data(connectionFailed)

    connectionFailed = False


def on_close(ws):
    print('Closed connection.')


def on_error(ws, err):
    print('Socket disconnected due to', err)
    print('Trying to reconnect socket...')
    global connectionFailed

    time.sleep(5)

    connectionFailed = True
    # Close current websocket to avoid overlapping multiple connections
    # when connection returns
    ws.close(status=1002)

    print('Trying to recover lost data...')
    init_socket()


def on_message(ws, message):
    try:
        kline = json.loads(message)['data']['k']  # Just load the kline

        print(kline['s'], '     ', end='\r') # See crypto symbol

        # Do calculations only when the candle is closed
        if kline['x']:
            print('\n', time.asctime(time.gmtime(time.time())), '\n')
            global candles, closes, ma, sma, rsi, wallet, targetStop

            crypto = kline['s']
            closes[crypto].append(float(kline['c']))
            candles[crypto].append(kline)
            
            # Update the candles only when the candles from all cryptos are
            # closed.
            # Because there is a crypto symbol that doesn't work,
            # we need to count until 74 (len-1) TODO
            # TODO take out the invalid crypto pair and make all() to avoid
            # traversing everything
            size = len(closes[crypto])
            if sum([len(close) == size for close in closes.values()]) == 74:
                updateCandles()
                
            # Update calculations
            for n in [6, 14, 50]:
                ma[crypto][n].append(average(closes[crypto], n))
                #print('\nMA'+str(n)+' append:', average(closes[crypto], n))
            
            for n in [20, 50]:            
                ema[crypto][n].append(updateEMA(closes[crypto][-1],
                                                ema[crypto][n][-1], n))

            rsi[crypto].append(relativeStrengthIndicator(closes[crypto],
                                                         RSI_PERIOD)[0])
            
            # If there are coins owned
            if wallet[crypto]:
                
                # If it is a long position
                if wallet[crypto][2]:
                    # Take profit.
                    if float(kline['h']) >= targetStop[crypto][1]:
                        sell(crypto, targetStop[crypto][1])
                    # Stop loss.
                    elif float(kline['l']) <= targetStop[crypto][0]:
                        sell(crypto, targetStop[crypto][0])
                
                # If it is a short position
                else:
                    # Take profit.
                    if float(kline['l']) <= targetStop[crypto][1]:
                        sell(crypto, targetStop[crypto][1])
                    # Stop loss.
                    elif float(kline['h']) >= targetStop[crypto][0]:
                        sell(crypto, targetStop[crypto][0])
                
            # Look for buying long
            elif isGoingToRise(crypto, verbose=False):
                price = closes[crypto][-1]
                
                stop = ema[crypto][50][-3]
                target = price + (price-stop)*RRRATIO
                
                targetStop[crypto] = [stop, target]
                
                if target/price > 1.001: # Avoid ultra low profit (>0.1%)
                    buy(crypto, True)
            
            # Look for buying short
            elif isGoingToFall(crypto, verbose=False):
                price = closes[crypto][-1]
                
                stop = ema[crypto][50][-3]
                target = price - (stop-price)*RRRATIO
                
                targetStop[crypto] = [stop, target]
                
                # Profit (% x1) > 0.001 <=> 1 - target/price > 0.001
                if target/price <= 0.999:
                    buy(crypto, False)

    except Exception as e:
        print('programming error at on_message:', e)


# Constants
RSI_PERIOD = 14
INIT_USDT = 1000
INIT_BNB = 10  # Needed to pay fees

MAX_CURRENT_CRYPTOS = 20 # Stop buying if this number of positions are open
MIN_USDT = 200  # Stop buying if this number is reached

RRRATIO = 1.5 # Risk Reward Ratio
LEVERAGE = 5 # Futures leverage
MAKERFEERATE = 0.00018
TAKERFEERATE = 0.00036

"""
A little explanation on how leverage positions are coded.
Opening a position is buying from now on, even it is a short position, it is
still considered buying.
Closing a position is selling from now on, even it is a short position, it is
still considered selling.

When buying:
    - The total USDT spent recorded on the code is the initial margin price.
    - The number of coins owned are the equivalent of margin*LEVERAGE.
    - Example: Buy 50€ of VET at 0.08 with LEVERAGE=5
        - usdt spent: 50 USDT
        - actual position price (not logged on tradeHistory): 50*5 = 250 USDT
        - coins owned: 250 USDT/0.08(USDT/VET) = 3125 VET

When selling:
    - The total USDT earned is the initial margin + profit.


"""

# Globals
candles = {}
closes = {}

# tresholds[crypto] = [sell_limit, stop_loss] (percentage x1)
thresholds = {}

# targetStop[crypto] = [stop_price, target_price] (price, not percentage)
targetStop = {}

lastTradeTime = {}

ma = {}
ema = {}
rsi = {}
#tradeHistory = []
currentCryptos = 0

""" Indicates how many coins are in the wallet
    wallet[crypto] = (amount, priceOfBuy, long)
        long = True if long position
               False if short position 
"""
wallet = {}
# State representing if the websocket connection is lost to request lost data.
connectionFailed = False

init_wallet()

# Initialize websocket connection
ws = None  # socket
init_socket()
