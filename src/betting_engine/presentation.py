"""English sportsbook terminology for current and archived API presentation.

Presentation only: the immutable historical records and model parameters are
not rewritten when changing the interface language.
"""
import re
from functools import lru_cache
from decimal import Decimal, ROUND_CEILING
from urllib.parse import urlsplit

TEXT = {
 'Teljes mérkőzés':'Full Game',
 'Hosszabbítással; moneyline döntetlennél a platform szabálya ellenőrizendő.':'Includes overtime. Check the sportsbook settlement rules if the Winner market can end in a tie.',
 'Érzékenységi forgatókönyv, nem statisztikai konfidenciaintervallum.':'Sensitivity scenario, not a statistical confidence interval.',
 'Sportadatból tanított modell; az odds csak az utólagos érték-összevetéshez kerül be.':'The model uses sports data. Market odds are used only to assess value after the probability estimate.',
 'becsült előny':'Estimated Value', 'előny bizonytalan':'Uncertain Edge', 'jobb ár szükséges':'Better Odds Needed',
 'visszavont / lejárt':'Withdrawn / Expired',
 'Az aktuális áron nincs becsült pozitív érték.':'No estimated positive expected value at the current odds.',
 'Beállított alapegység; bankroll hiányában nem személyre szabott.':'Uses your default unit stake. Set your bankroll for bankroll-based sizing.',
 'Konzervatív eséllyel számolt rész-Kelly, napi és fogadásonkénti korláttal.':'Fractional Kelly using the conservative probability estimate, capped by your daily limit and maximum stake.',
 'Erre a meccsre és piaccsaládra már rögzítettél fogadást; további tétet nem javasol.':'You already have an open bet on this game and market type. No additional stake is recommended.',
 'A megmaradt keretből vagy a rész-Kelly alapján nem adódik legalább 1 USD tét.':'Your remaining limit or fractional Kelly calculation does not support a stake of at least $1.',
 'A referencia-platform minimuma nagyobb ennél; ez a tét csak megfelelő minimumú másik platformon alkalmazható.':'This stake is below the reference platform minimum. Use a sportsbook that accepts this stake and offers the same market at qualifying odds.',
 'A kezdő dobó még nincs mindkét csapatnál megadva; a modell ligaátlagra húz vissza.':'One or both starting pitchers are unconfirmed. The model uses a league-average prior where needed.',
 'A meccsgyőzelmi modell előnye a konstans alapmodellhez képest még nem egyértelmű.':'The match-winner model has not clearly outperformed the constant-probability baseline.',
 'A publikáló modell paraméterei az értékelésben használt befagyasztott paraméterek; a csapatállapot friss eredményekből frissül.':'Published forecasts use the same frozen parameters as the evaluation. Team form updates with new results.',
 'A sérülések és teljes kezdőcsapat kvantitatív korrekciója még hiányzik.':'Full injury and starting-lineup adjustments are not yet included in the model.',
 'A sérüléslista több mint egy napos.':'The injury report is more than one day old.',
 'A sérüléslistán szerepel játékos; a konkrét státusz a részletek között látható, az esélyt még nem korrigálja.':'Players appear on the injury report. See their status below; these reports do not yet adjust the model probability.',
 'A történeti sportadatok utólag javított állapotból származhatnak; teljes korabeli hírarchívum nincs.':'Historical data may include later corrections. A complete archive of information available at each prediction time is not available.',
 'Az időrendi teszten az egyszerű Elo-becslés Brier-értéke jobb volt; a modellváltozat további ellenőrzést igényel.':'The simple Elo baseline had a better Brier score on the chronological test. This model needs further validation.',
 'Csapatszintű modell; nincs még betanított sérülés-, lineup- és játékosperc-hatás.':'Team-level model; injury, lineup and player-minute effects have not yet been trained.',
 'Kevés csapatelőzmény, erős visszahúzás a ligaátlaghoz.':'Limited team history; estimates are strongly shrunk toward the league average.',
 'Nincs történeti, végrehajtható árakon igazolt fogadási hozam.':'Betting profitability has not been verified using historical executable odds.',
 'Saját pontbecslés körüli félpontos küszöbök; nem történeti book-vonalak és nem hozamteszt.':'Half-point thresholds around model projections; these are not historical sportsbook lines or an ROI backtest.',
 'Készenlét':'Ready', 'Sportadatok ellenőrzése':'Checking sports data',
 'Sportstatisztikák és sérülésjelentések':'Updating stats and injury reports',
 'Sportmodellek tanítása':'Training sports models',
 'Napi újratanítás és időrendi ellenőrzés':'Daily retraining and chronological validation',
 'Saját meccselőrejelzések':'Generating game projections',
 'Elérhető piacok és aktuális árak':'Checking markets and live odds',
 'Tippek rangsorolása és tétértékelés':'Ranking picks and sizing stakes',
 'Frissítve':'Updated', 'Frissítési hiba':'Refresh failed',
 'Árforrás-kiesés':'Odds feed unavailable', 'Részleges piaci frissítés':'Partial odds coverage',
 'Később csatlakoztatható; jelenleg nincs üzenetküldés.':'Not connected. No Telegram messages are being sent.',
 'Az árforrás nem elérhető; nincs friss, ellenőrzött piaci ajánlás.':'The odds feed is unavailable. No fresh, verified market recommendations are available.',
 'Az árforrás részben elérhető; a piaci lefedettség hiányos.':'The odds feed is partially available. Market coverage is incomplete.',
 'A napi keret nem lehet nagyobb a bankrollnál.':'Your daily stake limit cannot exceed your bankroll.',
 'Ez a fogadás már le van zárva.':'This bet has already been settled.',
 'Ismeretlen ajánlás':'Pick not found', 'Ismeretlen naplótétel':'Bet not found',
 'Lejárt, már rögzített vagy az elvárt előnyt el nem érő ajánlat.':'This pick has expired, is already recorded, or does not meet your minimum estimated ROI.',
 'Polymarket pozícióhoz kézi elszámolást válassz; a részvénykifizetés nem sportsbook odds.':'Choose manual settlement for Polymarket. Share payouts do not follow standard sportsbook settlement.',
 'Feltétel: ugyanaz a teljes meccses piac és vonal, push esetén tétvissza; a megadott ár külső elérhetőségét ez nem ellenőrzi.':'Assumes the same Full Game market and line, with the stake refunded on a push. Availability of the entered odds is not verified.',
 'Feltétel: ugyanaz az esemény, név szerinti piac, időszak, kimenetel és vonal; push esetén tétvissza. A megadott ár külső elérhetőségét ez nem ellenőrzi.':'Assumes the exact same event, named market, period, outcome and line, with a stake refund on a push. Availability of the entered odds is not verified.',
 'Csak a rögzített tényleges fogadások eredménye. A rendszerköltség időarányos becslés; a modellteszt nem fogadási profit.':'Results include only recorded actual bets. System costs are prorated estimates; model test results are not betting profit.',
 'Nincs ellenőrzött meccsazonosítóval illeszthető sor':'No rows could be matched using verified game IDs.',
 'Első / kézzel indított fejlesztői modellkiadás':'Initial or manually initiated research model release',
 'Eltérő tesztcéladat; forrásfelülvizsgálat szükséges':'Test outcomes differ; review the source data.',
 'Kevesebb mint 12 közös teszthét':'Fewer than 12 shared test weeks',
 'Kevés közös időben elkülönített tesztmeccs':'Insufficient shared out-of-time test games',
 'Páros heti Brier-ellenőrzés; nem profitigazolás':'Paired weekly Brier comparison; not evidence of profitability',
 'Adatminőségi kizárás: gyanúsan állandó eredmények egy teljes adatcsomagban':'Data quality rejection: suspiciously constant scores across an entire batch.'
}
PATTERNS = [
 (r'A saját modell (.+) nyerési esélyt becsül erre az oldalra\.',r'Model-estimated win probability: \1.'),
 (r'Becsült hazai pontkülönbség: (.+); a választott hendikep teljesülési esélye (.+)\.',r'Projected home margin: \1. Estimated cover probability: \2.'),
 (r'Becsült összpontszám: (.+); a (.+) vonalra számolt esély (.+)\.',r'Projected game total: \1. Probability of the selected side at a total of \2: \3.'),
 (r'A referenciaárhoz tartozó becsült nettó hozam (.+), díjjal együtt\.',r'Estimated ROI at the reference price: \1, after fees.'),
 (r'Pihenő az előző meccs kezdésétől: hazai (.+), vendég (.+) nap \(14 napnál korlátozva\)\.',r'Days since the previous tip-off or start: home \1, away \2 (capped at 14 days).'),
 (r'Feltételes jelölt: csak legalább (.+) sportsbook oddson, azonos szabályok mellett\.',r'Watchlist: requires decimal odds of at least \1 on the same market and settlement rules.'),
 (r'Összpontszám felett (.+)',r'Over \1'),(r'Összpontszám alatt (.+)',r'Under \1')
]

@lru_cache(maxsize=16384)
def english_text(text):
    if text in TEXT:return TEXT[text]
    for pattern,replacement in PATTERNS:
        if re.fullmatch(pattern,text):return re.sub(pattern,replacement,text)
    # Some stake notes consist of two known sentences. Replace full phrases only.
    for source,target in TEXT.items():
        if len(source)>30 and source in text:text=text.replace(source,target)
    return text

# Verified against Stake's public market pages; see docs/STAKE_MARKETS.md.
STAKE_LEAGUE_PATHS = {
    'NBA':'basketball/usa/nba', 'WNBA':'basketball/usa/wnba',
    'NFL':'american-football/usa/nfl', 'MLB':'baseball/usa/mlb',
    'NHL':'ice-hockey/usa/nhl',
    'NCAAF':'american-football/usa/ncaa-regular','NCAAM':'basketball/usa/ncaa','NCAAW':'basketball/usa/ncaa-women',
}

def market_name(league,market,tip=None):
    from .contracts import split_market,SCOPE_LABELS
    period,base_market=split_market(market)
    if period!='full_game':
        if tip and tip.get('reference_contract_description'):return tip['reference_contract_description']
        prefix=SCOPE_LABELS[period]
        return prefix+' - '+{'moneyline':'Winner','three_way':'1x2','spreads':'Handicap','totals':'Total','home_total':'Home Team Total','away_total':'Away Team Total','odd_even':'Odd/Even','both_score':'Both Teams to Score','double_chance':'Double Chance','winner_total':'1x2 & Total','exact_total':'Total (Over-Exact-Under)','exact_score':'Correct Score'}.get(base_market,base_market)
    base={'moneyline':'Winner','spreads':'Handicap','totals':'Total','home_total':'Home Team Total','away_total':'Away Team Total','odd_even':'Odd/Even','both_score':'Both Teams to Score','double_chance':'Double Chance','winner_total':'1x2 & Total','exact_total':'Total (Over-Exact-Under)','exact_score':'Correct Score'}.get(market,market)
    if tip and market in ('home_total','away_total'):base=tip[market.split('_')[0]]+' Total'
    if league not in STAKE_LEAGUE_PATHS:return base
    scope={'MLB':'Extra Innings','NHL':'Overtime and Penalties'}.get(league,'Overtime')
    return f'{base} (Incl. {scope})'

def stake_market(t):
    from .contracts import split_market
    league=t.get('league')
    note={
        'NHL':'Includes overtime and penalty shootouts. Regulation-time and 1x2 markets are different bets.',
        'MLB':'Includes extra innings. Winner and Handicap/Total markets have different rules for shortened games; check Stake’s settlement rules.',
        'NFL':'Includes overtime. A tied two-way Winner market is void under Stake’s push rule.',
    }.get(league,'Includes overtime. Use the exact named match or team market; quarter and half markets settle separately.')
    scope,_=split_market(t['market'])
    if scope!='full_game':
        from .contracts import SCOPE_LABELS
        note={'inning_1':'Only runs in the 1st Inning count. The inning must be completed; an already determined Over follows Stake rules.',
            'innings_1_5':'Only runs in Innings 1 to 5 count. Stake requires five innings (4.5 if the home team leads); an already determined Over follows Stake rules.'}.get(scope,
            f'Only the named {SCOPE_LABELS[scope]} score counts. '+('Includes overtime.' if scope.endswith('_ot') else 'Excludes overtime and shootouts.'))
        note+=' A tie is a push for two-way Winner and a winning Draw in 1x2. Missing or incomplete segment results remain pending for settlement review.'
    from .overtime_contracts import overtime_alias
    alias = overtime_alias(league, t.get('reference_contract_description'), t.get('selection'))
    if alias == (t['market'], t.get('side')) and scope == 'regulation':
        note='Yes wins if regulation ends in a draw; No wins if it does not. Whether overtime is actually played does not change settlement. Verified completed regulation scores are required.'
        if league == 'NFL':
            note='In a completed NFL regular-season or playoff game, a regulation tie triggers overtime. Yes wins for a regulation tie; No wins for unequal regulation scores. Preseason and incomplete games are excluded from this adapter.'
    url=t.get('reference_url') or ''
    path=STAKE_LEAGUE_PATHS.get(league,'')
    try:
        parsed=urlsplit(url)
        if league.startswith('NCAA'):
            observed=t.get('stake_evidence',{}).get('league_path','')
            sport='american-football' if league=='NCAAF' else 'basketball'
            if re.fullmatch(sport+r'/usa/[a-z0-9-]*ncaa[a-z0-9-]*',observed):path=observed
        prefix='/sports/'+path+'/'
        event_url=url if (t.get('reference_platform')=='Stake.com' and parsed.scheme=='https'
            and parsed.netloc=='stake.com' and parsed.path.startswith(prefix)
            and parsed.path[len(prefix):] and not parsed.query and not parsed.fragment) else None
    except ValueError:event_url=None
    group_labels={'main':'Main','innings 1 to 5':'Innings 1 to 5','innings':'Innings',
                  'on the mound':'On The Mound','threeway':'Three Way','runs':'Runs',
                  'other':'Other','handicap':'Handicap','total':'Total','winner':'Winner','outright':'Outright',
                  'periods':'Periods','quarters':'Quarters','half':'Half','combo':'Combo','goals':'Goals',
                  'player props':'Player Props','points':'Points','specials':'Specials'}
    groups=[{**g,'label':group_labels.get(g['name'].lower(),g['name'])}
            for g in t.get('stake_evidence',{}).get('market_groups',[]) if isinstance(g,dict) and isinstance(g.get('name'),str) and g['name'].strip()]
    # Feed groups are contract evidence, not a guarantee of a visible website
    # tab. In particular NHL's internal "winner" group has no Winner tab.
    # Keep every group above for provenance; only known navigation names can
    # become a route. Never title-case an unknown group into a made-up tab.
    known_tabs=set(group_labels)-{'winner','outright'}
    if league=='NHL':
        known_tabs={'main','player props','periods','goals','combo'}
    routes=[g for g in groups if g['name'].lower() in known_tabs]
    primary=next((g for g in routes if g['name'].lower()=='main'),routes[0] if routes else None)
    return {
        'name':market_name(league,t['market'],t),
        'league_url':'https://stake.com/sports/'+path,
        'event_url':event_url,
        'bet_link_scope':'event' if event_url else None,
        'market_groups':groups,
        'tab':primary['label'] if primary else None,
        'navigation_source':'same_market_in_stake_feed_groups' if primary else None,
        'navigation_status':'recognized_group_route' if primary else 'website_tab_unconfirmed',
        'navigation_note':('The exact market is in this recognized feed group; the event link does not preselect a tab.' if primary else
                           'The exact market is present in the feed, but its website tab has not been confirmed. Do not substitute a different market or period.'),
        'bet_link_note':'Opens this match. Select the exact market and outcome shown; the bet slip is not prefilled.',
        'rules_url':'https://stake.com/policies/sportsbook',
        'scope_note':note,
        'availability_verified': t.get('reference_platform') == 'Stake.com',
        'availability_note': ('This exact market, line and outcome were present in the Stake feed when this pick was generated. Recheck the live price before placing the bet.'
                              if t.get('reference_platform') == 'Stake.com' else
                              'Stake market and odds availability is not verified for this record.'),
    }

def selection_name(t):
    from .contracts import split_market
    side=t.get('side');_,market=split_market(t['market']);team=t.get(side) if side in ('home','away') else None
    if market in ('moneyline','three_way') and team:return team
    if market=='three_way' and side=='draw':return 'Draw'
    if market=='spreads' and team and t.get('line') is not None:return f'{team} {t["line"]:+g}'
    if market in ('totals','home_total','away_total') and side in ('over','under') and t.get('line') is not None:
        return f'{side.title()} {t["line"]:g}'
    return english_text(t.get('selection',''))

def english_payload(value):
    if isinstance(value,str):return english_text(value)
    if isinstance(value,list):return [english_payload(v) for v in value]
    if isinstance(value,dict):
        result={k:english_payload(v) for k,v in value.items()}
        from .contracts import SUPPORTED
        if 'selection' in value and value.get('market') in SUPPORTED:
            result['selection']=selection_name(value)
            result['market_display']=market_name(value.get('league'),value['market'],value)
            if value.get('league') in STAKE_LEAGUE_PATHS:result['stake_market']=stake_market(value)
            if result.get('stake_note','').startswith('Watchlist:') and value.get('minimum_odds') is not None:
                threshold=Decimal(str(value['minimum_odds'])).quantize(Decimal('.01'),rounding=ROUND_CEILING)
                result['stake_note']=f'Watchlist: requires decimal odds of at least {threshold} on the same market and settlement rules.'
        return result
    return value
