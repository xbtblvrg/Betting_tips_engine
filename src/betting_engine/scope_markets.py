"""Exact standard Stake names. A separate validated model must authorize each."""

def named_scopes(league,home,away):
    result={}
    names=[('1x2','three_way'),('Winner','moneyline'),('Draw No Bet','moneyline'),
           ('Handicap','spreads'),('Asian Handicap','spreads'),('Total','totals'),
           ('Asian Total','totals'),('Odd/Even','odd_even'),('Both Teams to Score','both_score'),('Double Chance','double_chance'),
           ('1x2 & Total','winner_total'),('Winner & Total','winner_total'),
           ('Total (Over-Exact-Under)','exact_total'),('Correct Score','exact_score')]
    prefixes=[]
    if league in ('NBA','WNBA','NFL','NCAAF'):
        prefixes=[('1st Half','half_1'),('2nd Half','half_2')]+[(p+' Quarter',f'quarter_{i}') for i,p in enumerate(('1st','2nd','3rd','4th'),1)]
    if league in ('MLS','EPL','UCL'):
        prefixes=[('1st Half','half_1'),('2nd Half','half_2')]
    if league=='NHL':prefixes=[(p+' Period',f'period_{i}') for i,p in enumerate(('1st','2nd','3rd'),1)]
    for prefix,scope in prefixes:
        for name,kind in names:result[f'{prefix} - {name}']=(scope,kind)
        for team,side in ((home,'home'),(away,'away')):
            result[f'{prefix} - {team} Total']=(scope,side+'_total')
            result[f'{prefix} - {team} total']=(scope,side+'_total')
        if scope in ('half_2','quarter_4'):
            for name,kind in names:
                for suffix in ('Incl. Overtime','incl. Overtime','incl. overtime'):
                    result[f'{prefix} - {name} ({suffix})']=(scope+'_ot',kind)
    if league in ('NBA','WNBA','NHL','NFL','NCAAF'):
        for name,kind in names:result[name]=('regulation',kind)
        for team,side in ((home,'home'),(away,'away')):
            for tot in ('Total','total'):
                result[f'{team} {tot}']=('full_game',side+'_total')
                for suffix in ('Incl. Overtime','incl. Overtime','incl. overtime'):
                    result[f'{team} {tot} ({suffix})']=('full_game',side+'_total')
        if league=='NHL':
            for team,side in ((home,'home'),(away,'away')):result[team+' Total']=('regulation',side+'_total')
    if league=='MLB':
        for prefix,scope in [('1st Inning','inning_1'),('Innings 1 to 5','innings_1_5')]:
            for name,kind in names:result[f'{prefix} - {name}']=(scope,kind)
            for team,side in ((home,'home'),(away,'away')):
                for tot in ('Total','total'):
                    result[f'{prefix} - {team} {tot}']=(scope,side+'_total')
        result['1st inning - Winner']=('inning_1','moneyline')
    if league in ('ATP', 'WTA', 'CHALLENGER'):
        for prefix, scope in [('1st Set', 'period_1'), ('2nd Set', 'period_2')]:
            for name, kind in [('Winner', 'moneyline'), ('Total Games', 'totals'), ('Total', 'totals'), ('Game Handicap', 'spreads'), ('Handicap', 'spreads'), ('Set Handicap', 'spreads'), ('Correct Score', 'exact_score')]:
                result[f'{prefix} - {name}'] = (scope, kind)
    return result
