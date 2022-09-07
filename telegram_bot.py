from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
import json, time, requests, pprint
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from PIL import Image
import mplfinance as mpf
import pandas as pd

# TYPE YOUR TELEGRAM USER ID HERE, IN ORDER TO MAKE SURE ONLY THE MESSAGES
# ARE SENT TO THAT USER
USER = None

# Define exceptions
class Error(Exception):
    pass

class InvalidRangeError(Error):
    """Raised when the input trade range is not valid"""
    pass

class NoTradesYet(Error):
    """Raised when the input trade range is not valid"""
    pass


def getBnbPrice():
    # Get BNB price to calculate exact fees
    url = 'https://api.binance.com/api/v3/avgPrice?symbol=BNBUSDT'
    return float(requests.get(url).json()['price'])


def isDigit(n):
    try:
        int(n)
        return True
    except ValueError:
        return  False


def average(x, n):
    """ Return the average of last n samples of x. """
    return sum(x[-n:])/n


def EMA(x, n, smoothing=2):
    """ Calculate the exponential moving average of x in
    [0:n+1, 1:n+2, ..., l-n:l+1].
    
    The first EMA is a SMA (Simple Moving Average). """
    
    ema = [average(x[:n], n)] # The first sample is a SMA.
    
    p = smoothing / (n+1) # Rho, weight factor.
    
    for price in x[n:]:
        ema.append(price*p + ema[-1]*(1-p))
    
    return ema


def sendMessage(update, context, msg=None, photo=None, corrector=True):
    try:
        if photo:
            context.bot.send_photo(
                chat_id=update.effective_chat.id,
                caption=msg,
                photo=open(photo, 'rb'))
        else:
            if msg and corrector:
                # Omit reserved characters
                exceptions = ['`']
                reserved = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#','+',
                            '-', '=', '|', '{', '}', '.', '!']
                msg = ''.join(['\\'+s if s in reserved and s not in exceptions
                               else s for s in msg])
                
            context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=msg,
                parse_mode='MarkdownV2')
            
    except Exception as e:
        print('Error on sendMessage:', e)


def notClosedPositions(update, context):
    maxId = max([trade['id']
                             for trade in context.user_data['trades']])
    openPositions = []
    for tradeId in range(maxId+1):
        tradeBuy = tradeSell = False
        for x in context.user_data['trades']:
            if x['id'] == tradeId:
                # If buy trade already found
                if tradeBuy:
                    tradeSell = x
                    break
                else:
                    tradeBuy = x
                    
        if not tradeSell:
            openPositions.append(tradeId)
    
    sendMessage(update, context, 'Current positions open: '
                + ', '.join(str(x) for x in openPositions) if
                openPositions else 'none')


def sendThresholds(update, context, msg):
    crypto = msg[msg.find('of')+2:].strip()
                       
    crypto = (crypto.upper() if crypto[-4:].upper() == 'USDT' else
              crypto.upper()+'USDT')

    try:
       
        with open('thresholds.json', 'r') as file:
            thresholds = json.load(file)
        
        message = list('Thresholds of *' + crypto + '*: \n'
                    +'Sell limit: '
                    +(str(round(thresholds[crypto][0], 5))
                            if thresholds[crypto][0] else 'None')
                    +'\n'
                    +'Stop loss: '
                    +(str(round(thresholds[crypto][1], 5))
                            if thresholds[crypto][1] else 'None'))
        
        sendMessage(update, context, message)
    
    except:
        sendMessage(update, context, 'Thresholds not calculated.')


def sendTradeChart(update, context, tradeId):
    context.bot.sendChatAction(update.effective_chat.id, "upload_photo")

    try:
        with open('candles.json') as jsonFile:
            candles = json.load(jsonFile)
        
        tradeBuy = None
        tradeSell = None
        for x in context.user_data['trades']:
            if x['id'] == tradeId:
                # If buy trade already found
                if tradeBuy:
                    tradeSell = x
                    break
                else:
                    tradeBuy = x
        
        newCandles = candles[tradeBuy['crypto']]
        
        # Calculate EMA of candles
        closes = [float(x['c']) for x in newCandles]
        ema = {20: [None]*20, 50: [None]*50}
        
        for n in [20, 50]:
            ema[n].extend(EMA(closes, n))
        
        df = pd.DataFrame(newCandles)
        df = df[['t', 'T', 'o', 'c', 'h', 'l', 'v']]
        df.columns = ['OpenTime', 'CloseTime', 'Open', 'Close', 'High', 'Low', 'Volume']
        
        df['Open'] = pd.to_numeric(df['Open'], downcast='float')
        df['Close'] = pd.to_numeric(df['Close'], downcast='float')
        df['Low'] = pd.to_numeric(df['Low'], downcast='float')
        df['High'] = pd.to_numeric(df['High'], downcast='float')
        df['Volume'] = pd.to_numeric(df['Volume'], downcast='float')
        df['CloseTime'] = pd.to_numeric(df['CloseTime'], downcast='float')
        df['Index'] = pd.to_numeric([x for x in range(len(closes))])
        
        # Convert to local time zone
        df['OpenTime'] = df['OpenTime']+2*3600*1000
        
        df['Date'] = pd.to_datetime(df['OpenTime'], unit='ms')
        df.index = pd.DatetimeIndex(df['Date'])
        df.index.name = 'Date'
        
        buyTime = tradeBuy['time']*1000
        sellTime = tradeSell['time']*1000 if tradeSell else None
        
        stop = tradeBuy['stop']
        target = tradeBuy['target']

        positionBuy = [abs(df['CloseTime'][i]-buyTime) < 120000
                    for i in range(len(df))]
        
        positionSell = ([abs(df['CloseTime'][i]-sellTime) < 120000
                    for i in range(len(df))] if tradeSell else None)
        
        # obtain the candle that has less than 2 minute diff between buy time
        buyDate = df.loc[positionBuy].index # used later when plotting
        sellDate = df.loc[positionSell].index if tradeSell else None
        
        try:
            # Buy candle number
            buyIdx = int(df.loc[buyDate]['Index'])
            sellIdx = int(df.loc[sellDate]['Index']) if tradeSell else None
            maxIdx = len(closes)
            
            # Calculate how many and which candles to be plotted
            # interval is [initPosition, endPosition)
            
            # Trade finished
            if tradeSell:
                diff = sellIdx - buyIdx
                # Center small trade
                if diff < 48-15-15 and sellIdx + int((48-diff)/2) <= maxIdx:
                    initPosition = buyIdx - int((48-diff)/2)
                    endPosition = sellIdx + int((48-diff)/2)
                # Other traddes, plot 15 + buy + ... + sell + 15 or end
                else:
                    endPosition = maxIdx if sellIdx + 15 > maxIdx else sellIdx + 15
                    initPosition = (buyIdx - 15 if endPosition - buyIdx + 15 > 48
                                    else endPosition - 48)
            # Trade not finished, plot buy until end or last 48
            else:
                endPosition = maxIdx
                initPosition = maxIdx - 48 if buyIdx - 15 >= maxIdx - 48 else buyIdx - 15
            
            style = mpf.make_mpf_style(
                marketcolors={'candle': {'up':'#2ebd85', 'down':'#f6465d'}, 
                            'edge':   {'up':'#2ebd85', 'down':'#f6465d'},
                            'wick':   {'up':'#2ebd85', 'down':'#f6465d'},
                            'ohlc':   {'up':'#2ebd85', 'down':'#f6465d'},
                            'volume': {'up':'#2ebd85', 'down':'#f6465d'},
                            'vcedge': {'up':'#2ebd85', 'down':'#f6465d'},
                            'vcdopcod': False,
                            'alpha': 1},
                facecolor='#1f2630',
                edgecolor='#2b323d',
                figcolor='#1f2630',
                gridcolor='#2b323d',
                rc={'axes.labelcolor': '#858e9c',
                    'xtick.color': '#858e9c',
                    'ytick.color': '#858e9c'},
                style_name='binance-dark')
            
            wconfig = {}
            
            mpf.plot(df.loc[(df['Index'] >= initPosition) & (df['Index'] < endPosition)],
                    type='candle', style=style, ylabel='', return_width_config=wconfig,
                    closefig=True)
        
            # Adjust the space between candles    
            if wconfig['candle_width'] >= 0.65:
                toSum = 0.2
            elif wconfig['candle_width'] >= 0.6:
                toSum = 0.12
            else:
                toSum = 0
            
            wconfig['candle_width'] += toSum
            
            mpf.plot(df.loc[(df['Index'] >= initPosition) & (df['Index'] < endPosition)],
                    type='candle', style=style, ylabel='', update_width_config=wconfig,
                    addplot = [mpf.make_addplot(ema[20][initPosition:endPosition],
                                                color='#f1b90c', markersize=1),
                                mpf.make_addplot(ema[50][initPosition:endPosition],
                                                color='#a61b62', markersize=2)],
                                hlines=dict(hlines=[stop,target],colors=['#db4054',
                                                                        '#269e6f'],
                                            linestyle='--'),
                                vlines=dict(vlines=[buyDate[0]],
                                            linestyle='--', colors=['#b8b8b8']),
                    savefig='chart.png')
            
            # Crop the image because sometimes mplfinance sucks    
            img = Image.open('chart.png')
            
            w, h = img.size
            
            left, right, top, bottom = 70, w-60, 50, h-40
            
            img.crop((left, top, right, bottom)).save('chart.png')
            
            sendMessage(update, context,
                        '#Trade'+str(tradeId) + ' ' + tradeBuy['crypto'],
                        photo='chart.png')
        
        except:
            sendMessage(update, context, 'Sorry, that trade was too long ago 🥺')
    
    except:
        print('Candles.json not created.')
    
def sendTradeCharts(update, context, msg):
    try:
        if context.user_data['trades']:
            
            maxId = max([trade['id']
                             for trade in context.user_data['trades']])
            # ':' represents a range of numbers
            if ':' in msg:
                try:
                    start, end = [x.split(':') for x in msg.split()
                                  if any(char.isdigit() for char in x)][0]
                except:
                    raise InvalidRangeError
                
                # No values specified
                if not start:
                    raise InvalidRangeError
                if not end:
                    end = maxId
                
                start = int(start)
                end = int(end)
                
                # Negative indices
                if start < 0:
                    start = maxId + start + 1
                    
                if end < 0:
                    end = maxId + end + 1
                       
                ids = (range(int(start), int(end)+1)
                       if start != end else start)
                
                if not ids:
                    raise InvalidRangeError
                
            else:  
                ids = [int(word) for word in msg.split() if isDigit(word)]
                ids = [tradeId if tradeId >= 0 else maxId + tradeId + 1
                       for tradeId in ids]
            
            if ids:
                for tradeId in ids:
                    if 0 <= tradeId <= maxId:
                        sendTradeChart(update, context, tradeId)
    
                    else:
                        sendMessage(update, context,
                                    'Sorry, the last trade was #'+str(maxId))
            else:
                sendMessage(update, context, 'Specify the tradeId please')
    
        else:
            sendMessage(update, context, 'No trades yet 🥺')
            
    except InvalidRangeError:
        sendMessage(update, context, 'Sorry, this is not a valid range')

    except Exception as e:
        print('Error on sendTradeCharts:', e)
            

def summaryImage(update, context, today=False):
    try:
        context.bot.sendChatAction(update.effective_chat.id, "upload_photo")
        
        # Read file
        with open('tradeHistory.txt', 'r') as textFile:
            tradesText = textFile.readline()
        
        trades = json.loads('['+tradesText[1:]+']')
        
        if today:
            actualTime = time.localtime(time.time())
            startOfDay = time.mktime((actualTime[0],
                                      actualTime[1],
                                      actualTime[2],
                                      0, 0, 0,
                                      actualTime[6],
                                      actualTime[7],
                                      actualTime[8]))
        
            for i in range(len(trades)-1, -1, -1): # reversed range
                if trades[i]['time'] < startOfDay:
                    trades = trades[i+1:]
                    break
        
        if trades:
            
            nWin = nLoss = moneyWin = moneyLoss = total = bnb = 0
            
            line = []
            prices = []
            for trade in trades:
                if trade['action'] == 'Sell':
                    if trade['total'] > 50:
                        nWin += 1
                        moneyWin += (trade['total']-50)
                    else:
                        nLoss += 1
                        moneyLoss += (trade['total']-50)
                    total += (trade['total']-50)
                    line.append(total)
                    prices.append(trade['total'])
                bnb += trade['fee']
                
            
            bnbPrice = getBnbPrice()
            
            msg = ('```\nnWin: ' + str(nWin) + '\nnLoss: ' + str(nLoss)
                 + '\n\nmoney win: ' + str(round(moneyWin, 3))
                 + '\n    avg money win: '
                 + str(round(moneyWin/nWin, 3) if nWin > 0 else 0)
                 + '\n\nmoney lost: ' + str(round(moneyLoss, 3))
                 + '\n    avg money lost: '
                 + str(round(moneyLoss/nLoss, 3) if nLoss > 0 else 0)
                 + '\n\nmoneyEarnt: ' + str(round(moneyWin+moneyLoss, 3))
                 + '\nbnb: ' + str(round(bnb, 5)) + ' ('
                 + str(round(bnb*bnbPrice, 3)) + ' USDT)'
                 + '\n\nprofit after fees: '
                 + str(round(moneyWin+moneyLoss-bnb*bnbPrice, 3))
                 + '```')
            
            style = {'figure.facecolor': '#1f2630',
                     'figure.edgecolor': '#2b323d',
                     'savefig.facecolor': '#1f2630',
                     'savefig.edgecolor': '#2b323d',
                     'axes.facecolor': '#1f2630',
                     'axes.edgecolor': '#2b323d',
                     'grid.color': '#2b323d',
                     'axes.labelcolor': '#858e9c',
                     'xtick.color': '#858e9c',
                     'ytick.color': '#858e9c'}
            
            plt.close()
            plt.style.use(style)
            plt.plot(line, color='#f54266')
            plt.grid()
            plt.plot([0]*len(line), '--', color='#858e9c')
            plt.savefig('summary.png')
            
            if nWin > 0 or nLoss > 0:
                sendMessage(update, context,
                            'Today' if today else 'Total'+' Summary',
                            photo = 'summary.png')
                
                sendMessage(update, context, msg)
            else:
                raise NoTradesYet
            
        else:
            raise NoTradesYet
    
    except NoTradesYet:
        sendMessage(update, context, 'No trades yet 🥺')
        
    except Exception as e:
        sendMessage(update, context, 'Error while plotting:\n'+str(e))
            
    
def messageListener(update, context):
    if context.user_data['access']:
        try:
            msg = update.message.text.lstrip().lower()
            
            if 'thresholds' in msg and 'of' in msg:
                sendThresholds(update, context, msg)
            
            elif 'summary' in msg:
                summaryImage(update, context,
                                 today='today' in msg or 'new' in msg)
                
            elif 'open' in msg:
                notClosedPositions(update, context)
                        
            elif 'trade' in msg:
                sendTradeCharts(update, context, msg)
            
            elif 'random' in msg:
                randomFact(update, context)
                
            else:
                sendMessage(update, context, 'Sorry, I did not understand that')
                
        except Exception as e:
            print('Error on messageListener:', e)
                

def randomFact(update, context):
    url = 'https://uselessfacts.jsph.pl/random.json?language=en'
    
    fact = requests.get(url).json()['text']
    
    sendMessage(update, context, fact)


def load_candles(crypto):
    
    with open('candles.json') as jsonFile:
        candles = json.load(jsonFile)
    
    return candles[crypto]

def check_log(context):
    """
    Sends new log updates to the chat.

    """
    update = context.job.context['update']
    context = context.job.context['context']
    try:
        with open('tradeHistory.txt', 'r') as textFile:
            tradesText = textFile.readline()
        
        trades = json.loads('['+tradesText[1:]+']') # Delete first ','
        
        # Number of lines not sent
        newTrades = len(trades)-len(context.user_data['trades'])
        
        # If there are too much trades (telegram bot was restarted), sent last 50 
        avoidFlooding =  newTrades > 50
        
        if newTrades > 0:
            # Get only new trades
            tradesToSend = trades[-newTrades:]
        
            # Send the new trades not sent before
            for i, trade in enumerate(tradesToSend):
                # Send maximum 10 messages per second
                if i >= 5 and i%5 == 0 and (not avoidFlooding
                                            or newTrades - i <= 50):
                    context.bot.sendChatAction(update.effective_chat.id, "typing") 
                    time.sleep(0.8)
                
                try:
                    # This id identifies each trade
                    if trade['action'] == 'Buy':
                        trade['id'] = context.user_data['nTrades']
                        context.user_data['nTrades'] += 1
                    else:
                        # Find last buy and assign its id to this sell
                        for previousTrade in reversed(context.user_data['trades']):
                            if previousTrade['crypto'] == trade['crypto']:
                                trade['id'] = previousTrade['id']
                                break
                        
                    
                    context.user_data['trades'].append(trade)
                    
                    # Send only the last 50 trades if there are too much to send.
                    if not avoidFlooding or newTrades - i <= 50:
                        # Round to 8 decimals and delete extra 0 at the end and str.
                        # TODO SOULD NOT BE NECESSARY WHEN DECIMAL THING IS DONE               
                        for x in ('price', 'filled', 'fee', 'total'):
                            trade[x] = format(trade[x], '.8f').strip('0')
                            trade[x] = ('0'+trade[x] if trade[x][0] == '.'
                                        else trade[x]+'0' if trade[x][-1] == '.'
                                        else trade[x])
                        
                        
                        date = time.localtime(trade['time'])
                        date = ("{:02d}".format(date.tm_mon)
                                +'-'+"{:02d}".format(date.tm_mday)+' '
                                +"{:02d}".format(date.tm_hour)+':'
                                +"{:02d}".format(date.tm_min)+':'
                                +"{:02d}".format(date.tm_sec))
                        
                        message = list(('#Trade' + str(trade['id']) + '\n'+
                                '```\n'
                                + ('🟢 ' if trade['action'] == 'Buy' else '🔴 ')
                                + trade['crypto'][:-4] + '/USDT'
                                + ' '*(13-len(trade['crypto'])) + date
                                + '\n' + trade['position']
                                
                                + '\n\nPrice (USDT): '
                                + ' '*(13-len(trade['price'])) + trade['price']
                                
                                + '\nFilled (' + trade['crypto'][:-4] + '): '
                                + ' '*(20-len(trade['crypto'])-len(trade['filled']))
                                + trade['filled']
                                
                                + '\nFee (BNB):'
                                + ' '*(17- len(trade['fee'])) + trade['fee']
                                
                                + '\nTotal (USDT):'
                                + ' '*(14-len(trade['total'])) + trade['total']
                                + '```'))
                        
                        sendMessage(update, context, message)
        
                except Exception as e:
                    sendMessage(update, context,
                           'Could not send trade:\n'+str(e))
    
    except NoTradesYet:
        print('No trades yet 🥺')

def start(update, context):
    """
    Basically starts reading, receiving and sending trades.
    Also checks user.
    """
        
    context.user_data['access'] = update.effective_chat.id == USER
    context.user_data['trades'] = []
    context.user_data['nTrades'] = 0
    context.user_data['candles'] = {}
    
    if context.user_data['access']:
        sendMessage(update, context, 'Acces granted, bot initialized.')
        
        context.job_queue.start()
        context.job_queue.run_repeating(check_log, 10, 0,
                                        context={'update': update,
                                                 'context': context})
    else:
        sendMessage(update, context, 'Your user ID is: ' + str(update.effective_chat.id) + '.\nAcces denegated, exiting bot.')
        

def main():
    # Open the token and the dispatcher of our bot.
    Tkn = open('token.txt').read().strip()
    updater = Updater(token=Tkn, use_context=True)
    dispatcher = updater.dispatcher
    

    # Set the commands that our bot will handle.
    dispatcher.add_handler(CommandHandler('start', start))
    dispatcher.add_handler(MessageHandler(Filters.text, messageListener))
    
    updater.start_polling()
    updater.idle()
            
if __name__ == '__main__':
    main()
