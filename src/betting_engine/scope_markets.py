"""Exact standard Stake names. A separate validated model must authorize each."""

def named_scopes(league,home,away):
    result={}
    names=[('1x2','three_way'),('Winner','moneyline'),('Draw No Bet','moneyline'),
           ('Handicap','spreads'),('Asian Handicap','spreads'),('Total','totals'),
           ('Asian Total','totals'),('Odd/Even','odd_even'),('Both Teams to Score','both_score'),('Double Chance','double_chance'),
           ('1x2 & Total','winner_total'),('Winner & Total','winner_total'),
           ('Total (Over-Exact-Under)','exact_total'),('Correct Score','exact_score')]
    prefixes=[]
    if league in ('NBA','WNBA','NFL'):
        prefixes=[('1st Half','half_1'),('2nd Half','half_2')]+[(p+' Quarter',f'quarter_{i}') for i,p in enumerate(('1st','2nd','3rd','4th'),1)]
    if league=='NHL':prefixes=[(p+' Period',f'period_{i}') for i,p in enumerate(('1st','2nd','3rd'),1)]
    for prefix,scope in prefixes:
        for name,kind in names:result[f'{prefix} - {name}']=(scope,kind)
        for team,side in ((home,'home'),(away,'away')):result[f'{prefix} - {team} Total']=(scope,side+'_total')
        if scope in ('half_2','quarter_4'):
            for name,kind in names:
                for suffix in ('Incl. Overtime','incl. Overtime','incl. overtime'):
                    result[f'{prefix} - {name} ({suffix})']=(scope+'_ot',kind)
    if league in ('NBA','WNBA','NHL','NFL'):
        for name,kind in names:result[name]=('regulation',kind)
        if league=='NHL':
            for team,side in ((home,'home'),(away,'away')):result[team+' Total']=('regulation',side+'_total')
    if league=='MLB':
        for prefix,scope in [('1st Inning','inning_1'),('Innings 1 to 5','innings_1_5')]:
            for name,kind in names:result[f'{prefix} - {name}']=(scope,kind)
            for team,side in ((home,'home'),(away,'away')):result[f'{prefix} - {team} Total']=(scope,side+'_total')
        result['1st inning - Winner']=('inning_1','moneyline')
    return result
