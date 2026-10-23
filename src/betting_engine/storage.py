"""Durable publication, audit trail, manual bets and future notification outbox."""
import json,sqlite3,hashlib,math
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from .providers import DATA
from .domain import utcnow,parse_time
from datetime import datetime,time,timezone,timedelta
from zoneinfo import ZoneInfo
from .decisions import DEFAULT_SETTINGS,publication_limits
from .contracts import contract_key,contract_value,settlement_scores,equivalent_contract_ids,split_market,allocation_family
from .freshness import MAX_QUOTE_AGE_SECONDS
from .snapshots import encode_publication,decode_publication

class Store:
    def __init__(self,path=None):
        self.path=Path(path or DATA/'engine.sqlite3');self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as c:c.executescript('''
        CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS daily_target_checkpoints(id INTEGER PRIMARY KEY AUTOINCREMENT,
          effective_at TEXT NOT NULL,created_at TEXT NOT NULL,local_date TEXT NOT NULL,timezone TEXT NOT NULL,
          release_id TEXT NOT NULL,reason TEXT NOT NULL,prior_calendar_issued INTEGER NOT NULL,
          UNIQUE(effective_at,release_id));
        CREATE TABLE IF NOT EXISTS placement_checklist(tip_id TEXT PRIMARY KEY,placed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS recommendation_issuance(family TEXT PRIMARY KEY,tip_id TEXT NOT NULL,issued_at TEXT NOT NULL,league TEXT NOT NULL,start TEXT NOT NULL,stake REAL NOT NULL,odds REAL NOT NULL,source TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS system_decisions(family TEXT PRIMARY KEY,tip_id TEXT NOT NULL,issued_at TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',pnl REAL,outcome_evidence TEXT);
        CREATE TABLE IF NOT EXISTS reported_bet_imports(source_id TEXT PRIMARY KEY,signature TEXT NOT NULL,bet_ids TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bet_reconciliation(id INTEGER PRIMARY KEY AUTOINCREMENT,source_id TEXT NOT NULL,bet_id INTEGER NOT NULL,before_record TEXT,after_record TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS publications(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tips(id TEXT PRIMARY KEY,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,fingerprint TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,tip_id TEXT NOT NULL,fingerprint TEXT NOT NULL,created_at TEXT NOT NULL,event TEXT NOT NULL,payload TEXT NOT NULL,delivered INTEGER NOT NULL DEFAULT 0,UNIQUE(tip_id,fingerprint));
        CREATE TABLE IF NOT EXISTS prospective(game_id TEXT PRIMARY KEY,created_at TEXT NOT NULL,start TEXT NOT NULL,league TEXT NOT NULL,model_version TEXT NOT NULL,payload TEXT NOT NULL,outcome REAL,brier REAL);
        CREATE TABLE IF NOT EXISTS segment_prospective(game_id TEXT NOT NULL,scope TEXT NOT NULL,created_at TEXT NOT NULL,start TEXT NOT NULL,model_version TEXT NOT NULL,payload TEXT NOT NULL,outcome TEXT,brier REAL,PRIMARY KEY(game_id,scope));
        CREATE TABLE IF NOT EXISTS bets(id INTEGER PRIMARY KEY AUTOINCREMENT,tip_id TEXT NOT NULL,created_at TEXT NOT NULL,stake REAL NOT NULL,odds REAL NOT NULL,platform TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',pnl REAL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bet_outcomes(bet_id INTEGER PRIMARY KEY,league TEXT,market TEXT,model_version TEXT,result TEXT NOT NULL,probability REAL,push_probability REAL,brier REAL,log_loss REAL,evidence TEXT NOT NULL);
        ''')
        with self.db() as c:
            columns={r['name'] for r in c.execute('PRAGMA table_info(bets)')}
            if 'request_id' not in columns:c.execute('ALTER TABLE bets ADD COLUMN request_id TEXT')
            c.execute('CREATE UNIQUE INDEX IF NOT EXISTS bets_request_id ON bets(request_id) WHERE request_id IS NOT NULL')
            from .instruction_refill import initialize
            initialize(c)
    _held_contracts_cache = {}
    _held_allocation_families_cache = {}

    def invalidate_held_caches(self):
        key = str(self.path.resolve())
        Store._held_contracts_cache.pop(key, None)
        Store._held_allocation_families_cache.pop(key, None)
        try:
            from .in_memory_cache import GLOBAL_IN_MEMORY_CACHE
            GLOBAL_IN_MEMORY_CACHE.clear()
        except Exception:
            pass
    @contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=30);c.row_factory=sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA busy_timeout=30000')
        c.execute('PRAGMA synchronous=NORMAL');c.execute('PRAGMA cache_size=-32000');c.execute('PRAGMA temp_store=MEMORY')
        try:yield c;c.commit()
        except: c.rollback();raise
        finally:c.close()
    def _read_cfg(self):
        cfg = {}
        if self.path.parent.resolve() == DATA.resolve():
            cfg_file = Path('config/runtime_settings.json')
            if cfg_file.exists():
                try: cfg = json.loads(cfg_file.read_text(encoding='utf-8'))
                except Exception: cfg = {}
        return cfg

    def settings(self):
        cfg = self._read_cfg()
        with self.db() as c:r=c.execute('SELECT value FROM kv WHERE key=?',('settings',)).fetchone()
        db_settings = json.loads(r['value']) if r else {}
        from .anchor_learning import runtime_settings
        return runtime_settings({**DEFAULT_SETTINGS, **cfg, **db_settings}, self.path.parent)
    def save_settings(self,value):
        from .anchor_learning import RUNTIME_KEY,weight_epoch
        with weight_epoch(self.path.parent,exclusive=True):
            result={k:v for k,v in {**self.settings(),**value}.items() if k!=RUNTIME_KEY}
            with self.db() as c:c.execute('INSERT OR REPLACE INTO kv VALUES (?,?)',('settings',json.dumps(result)))
        self.invalidate_held_caches()
        return result
    def request_source_refresh(self,reason):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute("SELECT value FROM kv WHERE key='source_refresh_request'").fetchone()
            if old:return json.loads(old['value'])
            value={'requested_at':utcnow().isoformat(),'reason':reason}
            c.execute("INSERT INTO kv VALUES ('source_refresh_request',?)",(json.dumps(value),))
        return value
    def pending_source_refresh(self):
        with self.db() as c:r=c.execute("SELECT value FROM kv WHERE key='source_refresh_request'").fetchone()
        return json.loads(r['value']) if r else None
    def acknowledge_source_refresh(self,request):
        if request:
            with self.db() as c:c.execute("DELETE FROM kv WHERE key='source_refresh_request' AND value=?",(json.dumps(request),))
    def latest(self):
        with self.db() as c:r=c.execute('SELECT id,payload FROM publications ORDER BY id DESC LIMIT 1').fetchone()
        return {**decode_publication(r['payload']),'version':r['id']} if r else None

    def latest_version(self):
        with self.db() as c:r=c.execute('SELECT id FROM publications ORDER BY id DESC LIMIT 1').fetchone()
        return r['id'] if r else 0

    def compress_publication_history(self,limit=20):
        """Bounded, compare-and-swap conversion; never touches bets or decisions."""
        if not 1<=limit<=100:raise ValueError('Batch must contain 1 to 100 publications')
        with self.db() as c:
            rows=c.execute("SELECT id,payload FROM publications WHERE typeof(payload)='text' ORDER BY id LIMIT ?",(limit,)).fetchall()
        converted=0;saved=0
        for row in rows:
            original=decode_publication(row['payload']);encoded=encode_publication(original)
            if decode_publication(encoded)!=original:raise ValueError('Publication compression roundtrip failed')
            with self.db() as c:
                changed=c.execute('UPDATE publications SET payload=? WHERE id=? AND payload=?',(encoded,row['id'],row['payload'])).rowcount
            converted+=changed
            if changed:saved+=len(row['payload'].encode('utf-8'))-len(encoded)
        return {'converted':converted,'payload_bytes_saved':saved}

    def archived_candidates(self, limit=100):
        """Return recent pre-Stake model picks as non-actionable audit context.

        These records remain useful for comparing the model's signal with a
        later sportsbook feed, but their old market/price must never be
        presented as a current Stake offer.
        """
        now=utcnow();end=now+timedelta(hours=48);found={}
        # Read the durable tip ledger rather than repeatedly parsing full
        # publication snapshots. Withdrawn historical tips remain available
        # here after the active Stake feed has replaced the recommendation set.
        with self.db() as c:rows=c.execute('SELECT last_seen,payload FROM tips ORDER BY last_seen DESC LIMIT 500').fetchall()
        for row in rows:
            try:tip=json.loads(row['payload'])
            except (TypeError,json.JSONDecodeError):continue
            if tip.get('reference_platform') == 'Stake.com':continue
            try:start=parse_time(tip['start'])
            except (KeyError,TypeError,ValueError):continue
            if not now<start<=end or tip.get('id') in found:continue
            found[tip['id']]={**tip,'stake':0.,'stake_verified':False,
                              'stake_status':'Needs Stake Verification',
                              'archive_source':tip.get('reference_platform','Historical model feed'),
                              'archived_at':row['last_seen']}
            if len(found)>=limit:return list(found.values())
        return list(found.values())
    def publish(self,payload,require_guard=False):
        from .anchor_learning import weight_epoch
        now=utcnow().isoformat()
        with weight_epoch(self.path.parent),self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            now=utcnow().isoformat()
            saved=c.execute("SELECT value FROM kv WHERE key='settings'").fetchone()
            db_settings = json.loads(saved['value']) if saved else {}
            cfg = self._read_cfg()
            from .anchor_learning import runtime_settings
            active_settings=runtime_settings({**DEFAULT_SETTINGS, **db_settings, **cfg}, self.path.parent)
            if not require_guard:
                if active_settings.get('dynamic_event_limits') is not None:
                    raise ValueError('Dynamic exposure requires guarded atomic publication')
            if require_guard:
                from .governance import verify_publication
                from .outcome_sizing_runtime import verify_ledger_evidence
                verify_ledger_evidence(c,payload.get('recommendations',[])+payload.get('alternatives',[])+payload.get('evaluated_candidates',[]),active_settings,parse_time(now))
                active_policy=active_settings.get('recommendation_policy','strict')
                for tip in payload.get('recommendations',[])+payload.get('alternatives',[])+payload.get('evaluated_candidates',[]):
                    verify_publication(tip,parse_time(now),active_settings)
                    if tip.get('decision_guard',{}).get('recommendation_policy','strict')!=active_policy:
                        raise ValueError('Publication blocked: recommendation policy changed during review')
                    if tip.get('decision_guard',{}).get('model_pick_sizing_policy','unit_cap_v1')!=active_settings.get('model_pick_sizing_policy','unit_cap_v1'):
                        raise ValueError('Publication blocked: model-pick sizing policy changed during review')
                from .funding_policy import model_allocation_policy
                from .decisions import continuous_probability_allocation
                probability_flow=continuous_probability_allocation(active_settings)
                family_additions=model_allocation_policy(active_settings)=='distinct_family_v1'
                if any(t.get('decision_guard',{}).get('model_pick_allocation_policy','one_per_game_v1')!=model_allocation_policy(active_settings) for t in payload.get('recommendations',[])+payload.get('alternatives',[])+payload.get('evaluated_candidates',[])):
                    raise ValueError('Publication blocked: model-pick allocation policy changed during review')
                self._validate_publication_limits(c,payload,active_settings)
                # One new nominal-model allocation per game, checked under the
                # same write transaction as immutable issuance. Replaying the
                # identical original ticket remains idempotent.
                model_picks={(r['game_id'],r['tier']):r['tip_id'] for r in c.execute("SELECT tip_id,json_extract(payload,'$.game_id') game_id,json_extract(payload,'$.funding_basis.tier') tier FROM system_decisions WHERE json_extract(payload,'$.funding_basis.tier') IN ('model_pick','probability_pick') AND json_extract(payload,'$.stake')>0")}
                new_preference_games=set()
                for tip in {t['id']:t for t in payload.get('recommendations',[])+payload.get('alternatives',[])+payload.get('evaluated_candidates',[])}.values():
                    if not probability_flow and tip.get('stake',0)>0 and tip.get('funding_basis',{}).get('tier') in ('model_pick','probability_pick'):
                        if family_additions and tip['funding_basis']['tier']=='model_pick':
                            model_picks[(tip['game_id'],'model_pick')]=tip['id']
                            continue
                        key=(tip['game_id'],tip['funding_basis']['tier'])
                        if model_picks.get(key,tip['id'])!=tip['id']:
                            raise ValueError('Publication blocked: model-pick game allocation already issued')
                        if key not in model_picks:
                            if tip['game_id'] in new_preference_games:raise ValueError('Publication blocked: multiple new preference bets on one game')
                            new_preference_games.add(tip['game_id'])
                        model_picks[key]=tip['id']
                from .decisions import probability_preference_ids
                candidates=list({t['id']:t for t in payload.get('recommendations',[])+payload.get('alternatives',[])+payload.get('evaluated_candidates',[])}.values())
                if family_additions or probability_flow:
                    from .model_allocation import verify_family_publication
                    verify_family_publication(c,payload.get('recommendations',[])+payload.get('alternatives',[])+payload.get('evaluated_candidates',[]),active_settings)
                allowed=probability_preference_ids(candidates,active_settings,held_preference_allocations=model_picks)
                for tip in candidates:
                    if tip.get('stake',0)>0 and tip.get('funding_basis',{}).get('tier')=='probability_pick' and tip['id'] not in allowed:
                        raise ValueError('Publication blocked: probability preference superseded by a better current or previously issued model choice')
            previous=c.execute('SELECT payload FROM publications ORDER BY id DESC LIMIT 1').fetchone()
            oldpayload=decode_publication(previous['payload']) if previous else {}
            oldtips=oldpayload.get('evaluated_candidates') or oldpayload.get('recommendations',[])+oldpayload.get('alternatives',[])
            current=list({t['id']:t for t in payload.get('evaluated_candidates',[])+payload.get('recommendations',[])+payload.get('alternatives',[])}.values())
            active={t['id'] for t in current}
            for tip in oldtips:
                if tip['id'] in active:continue
                tip={**tip,'withdrawn_at':now,'stake':0.,'label':'withdrawn / expired'}
                raw=json.dumps(tip,ensure_ascii=False);fingerprint=hashlib.sha256(raw.encode()).hexdigest()
                c.execute('UPDATE tips SET fingerprint=?,payload=? WHERE id=?',(fingerprint,raw,tip['id']))
                c.execute('INSERT OR IGNORE INTO outbox(tip_id,fingerprint,created_at,event,payload) VALUES (?,?,?,?,?)',(tip['id'],fingerprint,now,'withdrawn',raw))
            for gid,forecast in payload.get('forecasts',{}).items():
                game=next((g for g in payload.get('games',[]) if g['id']==gid),None)
                if game and parse_time(game['start'])>parse_time(now):
                    c.execute('INSERT OR IGNORE INTO prospective(game_id,created_at,start,league,model_version,payload) VALUES (?,?,?,?,?,?)',(gid,now,game['start'],game['league'],forecast['model_version'],json.dumps(forecast,ensure_ascii=False)))
            for tip in current:
                signature={k:tip.get(k) for k in ['id','selection','model_version','label','stake']}
                signature['odds_bucket']=round(tip['reference_odds'],2)
                fingerprint=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()
                old=c.execute('SELECT fingerprint,first_seen FROM tips WHERE id=?',(tip['id'],)).fetchone()
                first=old['first_seen'] if old else now;tip['first_seen']=first
                raw=json.dumps(tip,ensure_ascii=False)
                c.execute('INSERT OR REPLACE INTO tips VALUES (?,?,?,?,?)',(tip['id'],first,now,fingerprint,raw))
                if not old or old['fingerprint']!=fingerprint:
                    eventkey=hashlib.sha256((fingerprint+(old['fingerprint'] if old else '')+now).encode()).hexdigest()
                    c.execute('INSERT OR IGNORE INTO outbox(tip_id,fingerprint,created_at,event,payload) VALUES (?,?,?,?,?)',(tip['id'],eventkey,now,'updated' if old else 'new',raw))
            for forecast in payload.get('segment_forecasts',{}).values():
                game=next((g for g in payload.get('games',[]) if g['id']==forecast['game_id']),None)
                if game and parse_time(game['start'])>parse_time(now):
                    c.execute('INSERT OR IGNORE INTO segment_prospective(game_id,scope,created_at,start,model_version,payload) VALUES (?,?,?,?,?,?)',
                              (game['id'],forecast['scope'],now,game['start'],forecast['model_version'],json.dumps(forecast,allow_nan=False)))
            self._auto_track(c,payload,now)
            for tip in payload.get('recommendations',[]):
                self.record_issuance(c,tip,now,settings=active_settings)
            c.execute('INSERT INTO publications(created_at,payload) VALUES (?,?)',(now,encode_publication(payload)))
            # Keep recent publications and outbox notifications bounded to prevent multi-gigabyte bloat
            c.execute('DELETE FROM publications WHERE id NOT IN (SELECT id FROM publications ORDER BY id DESC LIMIT 150)')
            c.execute('DELETE FROM outbox WHERE id NOT IN (SELECT id FROM outbox ORDER BY id DESC LIMIT 500)')
        self.invalidate_held_caches()

    @staticmethod
    def _validate_publication_limits(c,payload,settings):
        """Write-transaction anti-flood guard for the live execution list."""
        from .decisions import publication_limits, continuous_probability_allocation
        limits = publication_limits(settings)
        if limits is None:
            return
        funded=[]
        min_stake = 0.50 if (settings and settings.get('capital_defense', False)) else float(settings.get('unit_stake', 0.5) if settings else 0.5)
        max_stake = float(settings.get('max_bet', 8.0))
        for tip in {t.get('id'):t for t in payload.get('recommendations',[]) if isinstance(t,dict) and t.get('id')}.values():
            try:
                if (min_stake - 1e-6) <= float(tip.get('stake',0)) <= (max_stake + 1e-6) and tip.get('reference_platform')=='Stake.com' and not tip.get('alternative'):
                    funded.append(tip)
            except (TypeError,ValueError):
                continue
        is_v3 = settings.get('publication_limits_version') == 'v3'
        cycle_cap = math.inf if is_v3 else limits['max_new_funded_per_publication']
        if len(funded)>cycle_cap:
            raise ValueError(f'Publication blocked: maximum {cycle_cap} new funded recommendations per cycle')
        existing_ids={r['tip_id'] for r in c.execute('SELECT tip_id FROM recommendation_issuance')}
        open_event_counts={};open_event_stakes={};open_period_counts={}
        for row in c.execute("SELECT payload,stake FROM bets WHERE status='open'").fetchall():
            try:
                tip=json.loads(row['payload']);gid=tip.get('game_id');open_event_counts[gid]=open_event_counts.get(gid,0)+1;open_event_stakes[gid]=open_event_stakes.get(gid,0.)+float(row['stake'])
                scope,_=split_market(tip.get('market',''))
                if scope!='full_game':open_period_counts[gid]=open_period_counts.get(gid,0)+1
            except (TypeError,ValueError,json.JSONDecodeError):continue
        local=utcnow().astimezone(ZoneInfo(settings.get('timezone','Europe/Budapest')))
        day_start=datetime.combine(local.date(),time.min,tzinfo=local.tzinfo).astimezone(timezone.utc).isoformat()
        day_end=datetime.combine(local.date()+timedelta(days=1),time.min,tzinfo=local.tzinfo).astimezone(timezone.utc).isoformat()
        daily={}
        for row in c.execute("SELECT i.league,d.payload FROM recommendation_issuance i JOIN system_decisions d ON d.family=i.family WHERE i.issued_at>=? AND i.issued_at<?",(day_start,day_end)).fetchall():
            try:
                tip=json.loads(row['payload']);key=(row['league'],allocation_family(tip));daily[key]=daily.get(key,0)+1
            except (TypeError,ValueError,json.JSONDecodeError):continue
        new=[tip for tip in funded if tip.get('id') not in existing_ids]
        from .dynamic_event_limits import policy as dynamic_policy, verify_batch, read_held, limits_for
        dynamic=dynamic_policy(settings) is not None
        roster=read_held(c) if dynamic else []
        if dynamic:verify_batch(c,funded,settings)
        counts={};stakes={};periods={};families={}
        for tip in new:
            gid=tip.get('game_id');counts[gid]=counts.get(gid,0)+1;stakes[gid]=stakes.get(gid,0.)+float(tip['stake'])
            scope,_=split_market(tip.get('market',''))
            if scope!='full_game':periods[gid]=periods.get(gid,0)+1
            key=(tip.get('league'),allocation_family(tip));families[key]=families.get(key,0)+1
        from .model_allocation import held_inventory
        held_families = held_inventory(c)[0] if settings.get('publication_limits_version') == 'v3' else set()
        family_limit = int(settings['max_picks_per_family']) if settings.get('max_picks_per_family') is not None else 3
        event_family_counts = Counter((t.get('game_id'), allocation_family(t)) for t in roster if float(t.get('stake', 0)) > 0)
        cycle_family_counts = Counter()
        additions_enabled = settings.get('allow_event_additions', False)
        for tip in new:
            effective = limits_for(settings,tip,roster,new) if dynamic else publication_limits(settings, tip.get('league'))
            eff_family_limit = max(int(settings.get('max_picks_per_family', 3)), 3 if (effective and effective.get('max_per_event', 4) > 4) else 3)
            key=(tip.get('game_id'), allocation_family(tip))
            if not continuous_probability_allocation(settings) and effective.get('distinct_families_required') and key in held_families:
                raise ValueError('Publication blocked: sparse league requires distinct scope/market families')
            held_families.add(key)
            cycle_family_counts[key] += 1
            event_family_counts[key] += 1
            if cycle_family_counts[key] > 1:
                raise ValueError(f"Publication blocked: maximum 1 funded selection per market family per game within a publication cycle")
            if event_family_counts[key] > eff_family_limit:
                raise ValueError(f"Publication blocked: maximum {eff_family_limit} funded selections per market family per game")
        for gid,count in counts.items():
            limits = limits_for(settings,next(t for t in new if t['game_id']==gid),roster,new) if dynamic else publication_limits(settings, next(t.get('league') for t in new if t.get('game_id') == gid))
            if open_event_counts.get(gid,0)+count>limits['max_per_event']:raise ValueError(f"Publication blocked: maximum {limits['max_per_event']} funded selections per game")
            if open_event_stakes.get(gid,0.)+stakes[gid]>limits['max_stake_per_event']+1e-9:raise ValueError(f"Publication blocked: ${limits['max_stake_per_event']:.0f} maximum stake per game")
            if open_period_counts.get(gid,0)+periods.get(gid,0)>limits['max_period_per_event']:raise ValueError(f"Publication blocked: maximum {limits['max_period_per_event']} period/quarter selection per game")
        total=sum(daily.values())+len(new)
        base_limit=max(1,math.ceil(total*limits['max_daily_family_share']))
        expanded_limit=max(1,math.ceil(total*min(0.70, limits['max_daily_family_share']*2.0)))
        from .decisions import is_profitable_or_alpha, is_super_alpha
        for key,count in families.items():
            tips_for_key = [t for t in new if (t.get('league'), allocation_family(t)) == key]
            is_super = is_v3 and any(is_super_alpha(t) for t in tips_for_key)
            is_prof = is_v3 and any(is_profitable_or_alpha(t) for t in tips_for_key)
            fam_limit = math.inf if is_super else (expanded_limit if is_prof else base_limit)
            if daily.get(key,0)+count>fam_limit:
                raise ValueError(f"Publication blocked: market-family daily share exceeds {limits['max_daily_family_share']:.0%}")
    @staticmethod
    def record_issuance(c,tip,issued_at,source='publication',settings=None):
        # Count an actually funded recommendation once, never a watchlist row,
        # duplicate ticket or requote of the identical settlement contract.
        min_stake = 0.50 if (settings and settings.get('capital_defense', False)) else float(settings.get('unit_stake', 0.5) if settings else 0.5)
        max_stake = float(settings.get('max_bet', 8.0)) if settings else 8.0
        try:
            stake = float(tip.get('stake', 0))
        except (TypeError, ValueError):
            return
        if tip.get('reference_platform')!='Stake.com' or not tip.get('stake_evidence') or not ((min_stake - 1e-6) <= stake <= (max_stake + 1e-6)) or tip.get('alternative'):return
        if not tip.get('game_id') or not tip.get('market') or not tip.get('start'):return
        if tip.get('withdrawn_at'):return
        try:
            if min(parse_time(tip['start']),parse_time(tip['valid_until']))<=parse_time(issued_at):return
        except (KeyError,ValueError,TypeError):return
        c.execute('INSERT OR IGNORE INTO recommendation_issuance VALUES (?,?,?,?,?,?,?,?)',
                  (contract_key(tip),tip['id'],parse_time(issued_at).astimezone(timezone.utc).isoformat(),tip['league'],tip['start'],tip['stake'],tip['reference_odds'],source))
        c.execute('INSERT OR IGNORE INTO system_decisions(family,tip_id,issued_at,payload) VALUES (?,?,?,?)',
                  (contract_key(tip),tip['id'],parse_time(issued_at).astimezone(timezone.utc).isoformat(),json.dumps({**tip,'ownership':'engine','ownership_source':source},ensure_ascii=False,allow_nan=False)))

    def settle_system_decisions(self,games):
        final={g.id:g for g in games if g.state=='final'}
        with self.db() as c:
            for row in c.execute("SELECT * FROM system_decisions WHERE status='open'").fetchall():
                tip=json.loads(row['payload']);game=final.get(tip['game_id'])
                if not game:continue
                try:
                    hs,aws,score_evidence=settlement_scores(tip,game)
                    value=contract_value(tip['market'],tip['side'],tip.get('line'),hs,aws)
                except ValueError:continue
                outcome='won' if value>0 else 'lost' if value<0 else 'push'
                pnl=tip['stake']*(tip['reference_odds']-1) if value>0 else -tip['stake'] if value<0 else 0.
                evidence={'home_score':hs,'away_score':aws,'score_scope':score_evidence,'source':game.source,'fetched_at':game.fetched_at,'settled_at':utcnow().isoformat(),'basis':'Original issued decision at published odds; not an account-verified return.'}
                c.execute('UPDATE system_decisions SET status=?,pnl=?,outcome_evidence=? WHERE family=? AND status=?',(outcome,pnl,json.dumps(evidence),row['family'],'open'))
                if outcome == 'lost':
                    try:
                        from .tier_state_machine import enqueue_rd_repair_job
                        from .segment_scoreboard import segment_key_from_payload
                        seg_key = segment_key_from_payload(tip)
                        if seg_key:
                            enqueue_rd_repair_job(
                                conn=c,
                                segment_key=seg_key,
                                league=tip.get('league'),
                                failure_mode='SETTLED_LOSS_TRIGGER',
                                remedy='Automatic post-loss forensic investigation and crucible repair'
                            )
                    except Exception:
                        pass

    def system_decisions(self):
        with self.db() as c:rows=c.execute('SELECT * FROM system_decisions ORDER BY issued_at DESC').fetchall()
        return [{**dict(r),'payload':json.loads(r['payload']),'outcome_evidence':json.loads(r['outcome_evidence']) if r['outcome_evidence'] else None} for r in rows]

    def adopt_legacy_reported_decisions(self):
        # The old main list displayed some zero-stake candidates as recommendations.
        # Own those selections too when the user explicitly reported placing them;
        # never relabel them as originally funded or inflate daily issuance counts.
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            rows=c.execute("SELECT * FROM bets WHERE json_extract(payload,'$.tracking_source')='user_reported_placed' ORDER BY id").fetchall()
            for row in rows:
                tip=json.loads(row['payload'])
                if tip.get('stake',0)>0:self.record_issuance(c,tip,tip.get('created_at',row['created_at']),'original_ledger_snapshot_reconstruction')
            before=c.total_changes
            for row in rows:
                tip=json.loads(row['payload'])
                snapshot={**tip,'original_recommendation_stake':tip.get('stake',0),
                          'stake':row['stake'],'reference_odds':row['odds'],'ownership':'engine',
                          'ownership_source':'legacy_displayed_selection_user_reported_placed',
                          'ownership_note':'Originally displayed by the engine without a positive stake allocation. Included in accountability using the reported ticket terms; not counted as a funded daily issuance.'}
                c.execute('INSERT OR IGNORE INTO system_decisions(family,tip_id,issued_at,payload) VALUES (?,?,?,?)',
                          (contract_key(tip),tip['id'],tip.get('created_at',row['created_at']),json.dumps(snapshot,ensure_ascii=False,allow_nan=False)))
            return c.total_changes-before

    def migrate_contract_identity(self):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            for table in ('recommendation_issuance','system_decisions'):
                c.execute(f"UPDATE {table} SET family='contract:' || tip_id WHERE family!='contract:' || tip_id")
    def reset_daily_progress(self,since,*,release_id,reason):
        """Start today's delivery target at an audited release checkpoint.

        This never deletes, reissues or changes any wager or original issuance.
        The checkpoint expires at the next local midnight. Only an explicit
        caller resets it; ordinary restarts/deployments never reset the target.
        """
        settings=self.settings();now=utcnow();zone=ZoneInfo(settings['timezone'])
        stamp=parse_time(since);local=now.astimezone(zone)
        start=datetime.combine(local.date(),time.min,tzinfo=zone).astimezone(timezone.utc)
        if not start<=stamp<=now:raise ValueError('Checkpoint must be within the current local day and not in the future')
        if not isinstance(release_id,str) or not release_id.strip() or not isinstance(reason,str) or not reason.strip():
            raise ValueError('Checkpoint requires release identity and reason')
        stamp=stamp.astimezone(timezone.utc).isoformat()
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT * FROM daily_target_checkpoints WHERE effective_at=? AND release_id=?',(stamp,release_id)).fetchone()
            if old:return dict(old)
            prior=c.execute('SELECT effective_at FROM daily_target_checkpoints WHERE local_date=? AND timezone=? ORDER BY id DESC LIMIT 1',(str(local.date()),settings['timezone'])).fetchone()
            if prior and parse_time(prior['effective_at'])>parse_time(stamp):raise ValueError('Checkpoint cannot move backwards')
            count=c.execute('SELECT COUNT(*) FROM recommendation_issuance WHERE issued_at>=? AND issued_at<?',(start.isoformat(),stamp)).fetchone()[0]
            row=c.execute('INSERT INTO daily_target_checkpoints(effective_at,created_at,local_date,timezone,release_id,reason,prior_calendar_issued) VALUES(?,?,?,?,?,?,?)',
                (stamp,now.isoformat(),str(local.date()),settings['timezone'],release_id,reason,count))
            return dict(c.execute('SELECT * FROM daily_target_checkpoints WHERE id=?',(row.lastrowid,)).fetchone())

    def daily_progress(self):
        settings=self.settings();local=utcnow().astimezone(ZoneInfo(settings['timezone']))
        start=datetime.combine(local.date(),time.min,tzinfo=local.tzinfo).astimezone(timezone.utc)
        end=datetime.combine(local.date()+timedelta(days=1),time.min,tzinfo=local.tzinfo).astimezone(timezone.utc)
        with self.db() as c:
            if not c.in_transaction:c.execute('BEGIN')
            checkpoint=c.execute('SELECT * FROM daily_target_checkpoints WHERE local_date=? AND timezone=? ORDER BY id DESC LIMIT 1',(str(local.date()),settings['timezone'])).fetchone()
            since=max(start,parse_time(checkpoint['effective_at'])) if checkpoint else start
            rows=c.execute('SELECT league,COUNT(*) n FROM recommendation_issuance WHERE issued_at>=? AND issued_at<? GROUP BY league',(since.isoformat(),end.isoformat())).fetchall()
            games=c.execute("SELECT i.league,COUNT(DISTINCT json_extract(d.payload,'$.game_id')) n FROM recommendation_issuance i JOIN system_decisions d ON d.family=i.family WHERE i.issued_at>=? AND i.issued_at<? GROUP BY i.league",(since.isoformat(),end.isoformat())).fetchall()
            calendar_count=c.execute('SELECT COUNT(*) FROM recommendation_issuance WHERE issued_at>=? AND issued_at<?',(start.isoformat(),end.isoformat())).fetchone()[0]
            issuance_rows=c.execute("SELECT i.tip_id,i.league,i.start,i.issued_at,json_extract(d.payload,'$.market') market FROM recommendation_issuance i JOIN system_decisions d ON d.family=i.family WHERE i.issued_at>=? AND i.issued_at<? ORDER BY i.start,i.issued_at,i.tip_id",(start.isoformat(),end.isoformat())).fetchall()
        count=sum(r['n'] for r in rows);target=settings.get('daily_pick_target',15)
        return {'date':local.date().isoformat(),'timezone':settings['timezone'],'target':target,'issued':count,
                'first_issued_today':count,
                'shortfall':max(0,target-count),'by_league':{r['league']:r['n'] for r in rows},
                'distinct_games':sum(r['n'] for r in games),'games_by_league':{r['league']:r['n'] for r in games},
                'counting_since':since.isoformat(),'counting_basis':'since_release' if checkpoint else 'calendar_day',
                'calendar_day_issued':calendar_count,'previously_issued_today':calendar_count-count,
                'issuance_rows':[dict(r) for r in issuance_rows],
                'checkpoint':dict(checkpoint) if checkpoint else None,
                'checkpoint_state':'active' if checkpoint else 'none',
                'basis':'First funded issuance of each exact contract since the explicit release checkpoint today.' if checkpoint else 'First funded issuance of each exact contract, by publication date. Additional lines may be correlated; repriced duplicates and research candidates are excluded.'}
    def _auto_track(self,c,payload,now):
        row=c.execute("SELECT value FROM kv WHERE key='settings'").fetchone()
        db_settings = json.loads(row['value']) if row else {}
        cfg = self._read_cfg()
        from .anchor_learning import runtime_settings
        settings=runtime_settings({**DEFAULT_SETTINGS, **db_settings, **cfg}, self.path.parent)
        if not settings.get('auto_track_recommendations'):return
        from .dynamic_event_limits import policy as dynamic_policy, read_held, limits_for
        dynamic=dynamic_policy(settings) is not None
        dynamic_roster=read_held(c) if dynamic else []
        issued={r['tip_id'] for r in c.execute('SELECT tip_id FROM recommendation_issuance')} if dynamic else set()
        dynamic_batch=[t for t in payload.get('recommendations',[]) if t.get('stake',0)>0 and t['id'] not in issued] if dynamic else []
        local=parse_time(now).astimezone(ZoneInfo(settings['timezone']))
        day_start=datetime.combine(local.date(),time.min,tzinfo=local.tzinfo).astimezone(timezone.utc).isoformat()
        committed=c.execute("SELECT COALESCE(SUM(stake),0) FROM bets WHERE created_at>=? OR status='open'",(day_start,)).fetchone()[0]
        # All previously recorded contracts count for duplicate prevention,
        # even if manually settled before kickoff. A qualified changed line may
        # be issued separately when event additions are enabled.
        existing=[json.loads(r['payload']) for r in c.execute('SELECT payload FROM bets')]
        families={(t.get('game_id'),t.get('market')) for t in existing}
        contracts={t.get('id') for t in existing};additions=settings.get('allow_event_additions',False)
        policy=publication_limits(settings)
        event_stakes=Counter();period_counts=Counter()
        exposure_records=dynamic_roster if dynamic else existing
        for old in exposure_records:
            try:
                gid=old.get('game_id');event_stakes[gid]+=float(old.get('stake',0))
                scope,_=split_market(old.get('market',''))
                if scope!='full_game':period_counts[gid]+=1
            except (TypeError,ValueError):
                continue
        event_counts=Counter(t.get('game_id') for t in exposure_records)
        diverse_families={(t.get('game_id'),allocation_family(t)) for t in existing}
        cycle_funded=0
        daily_family_counts=Counter();family_new=Counter()
        allowed_ids=None
        if policy:
            day_end=datetime.combine(local.date()+timedelta(days=1),time.min,tzinfo=local.tzinfo).astimezone(timezone.utc).isoformat()
            for issued in c.execute("SELECT i.league,d.payload FROM recommendation_issuance i JOIN system_decisions d ON d.family=i.family WHERE i.issued_at>=? AND i.issued_at<?",(day_start,day_end)).fetchall():
                try:
                    previous=json.loads(issued['payload'])
                    daily_family_counts[(issued['league'],allocation_family(previous))]+=1
                except (TypeError,ValueError,json.JSONDecodeError):
                    continue
            # First construct the usable, unique candidates and apply every
            # non-family cap without writing.  The final family-share pass is
            # then a fixed point over the candidates that could actually be
            # tracked.  This keeps the direct fallback path equivalent to the
            # atomic publication guard and prevents partial inserts.
            usable_ids=set();usable_families=set();usable_candidates=[]
            for candidate in payload.get('recommendations',[]):
                try:
                    if not isinstance(candidate,dict):continue
                    candidate_id=candidate.get('id')
                    amount=float(candidate.get('stake',0));odds=float(candidate.get('reference_odds',0))
                    evidence=candidate.get('stake_evidence',{})
                    if (not candidate_id or candidate_id in contracts or candidate_id in usable_ids
                            or not candidate.get('game_id') or not candidate.get('market')
                            or isinstance(candidate.get('stake'),bool) or not isinstance(candidate.get('stake'),(int,float))
                            or isinstance(candidate.get('reference_odds'),bool) or not isinstance(candidate.get('reference_odds'),(int,float))
                            or candidate.get('reference_platform')!='Stake.com'
                            or candidate.get('alternative') or candidate.get('withdrawn_at')
                            or not 0.5<=amount<=min(8,settings['max_bet'])
                            or not math.isfinite(amount) or not math.isfinite(odds) or odds<=1
                            or not evidence or isinstance(evidence.get('decimal_odds'),bool)
                            or not isinstance(evidence.get('decimal_odds'),(int,float))
                            or not math.isfinite(float(evidence.get('decimal_odds',0)))
                            or abs(float(evidence.get('decimal_odds',0))-odds)>1e-8):continue
                    if min(parse_time(candidate['start']),parse_time(candidate['valid_until']))<=parse_time(now):continue
                    if not 0<=(parse_time(now)-parse_time(evidence['fetched_at'])).total_seconds()<=MAX_QUOTE_AGE_SECONDS:continue
                    family=(candidate.get('game_id'),candidate.get('market'))
                    if not additions and (family in families or family in usable_families):continue
                    usable_ids.add(candidate_id);usable_families.add(family);usable_candidates.append(candidate)
                except (KeyError,TypeError,ValueError,OverflowError):
                    continue
            # Remove candidates that cannot survive event, period, cycle or
            # daily-risk limits before calculating the family denominator.
            eligible=[];prospective_events=Counter(event_counts)
            prospective_stakes=Counter(event_stakes);prospective_periods=Counter(period_counts)
            prospective_committed=committed
            for candidate in usable_candidates:
                policy=limits_for(settings,candidate,dynamic_roster,dynamic_batch) if dynamic else publication_limits(settings, candidate.get('league'))
                gid=candidate['game_id'];amount=float(candidate['stake']);scope,_=split_market(candidate.get('market',''))
                if len(eligible)>=policy['max_new_funded_per_publication']:continue
                diverse_key=(gid,allocation_family(candidate))
                if settings.get('family_selection_policy') not in ('continuous_probability_v2', 'continuous_probability_v3', 'continuous_probability_v4') and policy.get('distinct_families_required') and diverse_key in diverse_families:continue
                if prospective_events[gid]>=policy['max_per_event']:continue
                if prospective_stakes[gid]+amount>policy['max_stake_per_event']+1e-9:continue
                if scope!='full_game' and prospective_periods[gid]>=policy['max_period_per_event']:continue
                if settings.get('daily_risk_limit') is not None and prospective_committed+amount>settings['daily_risk_limit']+1e-8:continue
                diverse_families.add(diverse_key)
                eligible.append(candidate);prospective_events[gid]+=1;prospective_stakes[gid]+=amount
                if scope!='full_game':prospective_periods[gid]+=1
                prospective_committed+=amount
            selected=eligible
            while True:
                family_limit=max(1,math.ceil((sum(daily_family_counts.values())+len(selected))*policy['max_daily_family_share']))
                counts=Counter(daily_family_counts);survivors=[]
                for candidate in selected:
                    key=(candidate.get('league'),allocation_family(candidate))
                    if counts[key]>=family_limit:continue
                    counts[key]+=1;survivors.append(candidate)
                if len(survivors)==len(selected):break
                selected=survivors
            allowed_ids={candidate['id'] for candidate in selected}
            family_limit=max(1,math.ceil((sum(daily_family_counts.values())+len(selected))*policy['max_daily_family_share']))
        for tip in payload.get('recommendations',[]):
            if policy and (not isinstance(tip,dict) or tip.get('id') not in allowed_ids):continue
            policy=limits_for(settings,tip,dynamic_roster,dynamic_batch) if dynamic else publication_limits(settings, tip.get('league'))
            amount=tip.get('stake',0);odds=tip.get('reference_odds',0)
            if not(0.5<=amount<=min(8,settings['max_bet']) and math.isfinite(odds) and odds>1):continue
            if tip.get('reference_platform')!='Stake.com' or tip.get('alternative') or tip.get('withdrawn_at'):continue
            evidence=tip.get('stake_evidence',{})
            if not evidence or abs(evidence.get('decimal_odds',0)-odds)>1e-8:continue
            try:
                if min(parse_time(tip['start']),parse_time(tip['valid_until']))<=parse_time(now):continue
                quote_age_limit=MAX_QUOTE_AGE_SECONDS if policy else max(300,settings['refresh_seconds']*2)
                if (parse_time(now)-parse_time(evidence['fetched_at'])).total_seconds()>quote_age_limit:continue
            except (KeyError,ValueError,TypeError):continue
            family=(tip['game_id'],tip['market'])
            if tip['id'] in contracts or (not additions and family in families):continue
            event_cap=(policy['max_per_event'] if policy else settings.get('max_per_event'))
            if event_cap is not None and event_counts[tip['game_id']]>=event_cap:continue
            if policy:
                if cycle_funded>=policy['max_new_funded_per_publication']:continue
                if event_stakes[tip['game_id']]+amount>policy['max_stake_per_event']+1e-9:continue
                scope,_=split_market(tip.get('market',''))
                if scope!='full_game' and period_counts[tip['game_id']]>=policy['max_period_per_event']:continue
                family_key=(tip.get('league'),allocation_family(tip))
                if (daily_family_counts[family_key]+family_new[family_key]>=family_limit):continue
            limit=settings.get('daily_risk_limit')
            if limit is not None and committed+amount>limit+1e-8:continue
            recorded={**tip,'ledger_contract':'sportsbook','tracking_source':'auto_assumed_placed',
                      'execution_verified':False,'odds_basis':'recommendation_snapshot',
                      'recorded_at':now,'tracking_note':'Assumed placed manually on Stake under your standing instruction. Uses the recommended stake and observed odds; no Stake account confirmation.'}
            c.execute('INSERT INTO bets(tip_id,created_at,stake,odds,platform,payload,request_id) VALUES (?,?,?,?,?,?,?)',
                      (tip['id'],now,amount,odds,'Stake.com',json.dumps(recorded,ensure_ascii=False),f'auto-stake:{tip["id"]}'))
            families.add(family);contracts.add(tip['id']);event_counts[tip['game_id']]+=1;committed+=amount
            event_stakes[tip['game_id']]+=amount;cycle_funded+=1
            if policy and scope!='full_game':period_counts[tip['game_id']]+=1
            if policy:family_new[family_key]+=1
    def get_tip(self,id):
        with self.db() as c:r=c.execute('SELECT payload FROM tips WHERE id=?',(id,)).fetchone()
        return json.loads(r['payload']) if r else None
    def placement_marks(self):
        with self.db() as c:rows=c.execute('SELECT tip_id,placed_at FROM placement_checklist').fetchall()
        return {r['tip_id']:r['placed_at'] for r in rows}
    def mark_placed(self,tip_id,placed):
        from .instruction_refill import mark_placed
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            return mark_placed(c,tip_id,placed)
    def pending_instruction_refill(self):
        from .instruction_refill import pending
        with self.db() as c:return pending(c)
    def claim_instruction_refill(self):
        from .instruction_refill import claim
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            return claim(c)
    def complete_instruction_refill(self,receipt,error=None):
        from .instruction_refill import complete
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            return complete(c,receipt,error=error)
    def recover_instruction_refill(self):
        from .instruction_refill import recover_interrupted
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            return recover_interrupted(c)
    def instruction_refill_status(self,request_id=None):
        from .instruction_refill import status
        with self.db() as c:return status(c,request_id)
    def add_bet(self,tip_id,stake,odds,platform,request_id=None,settlement_mode="manual"):
        tip=self.get_tip(tip_id)
        if not tip:raise ValueError('Unknown recommendation')
        tip={**tip,'ledger_contract':settlement_mode}
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if request_id:
                existing=c.execute('SELECT id,tip_id,stake,odds,platform,payload FROM bets WHERE request_id=?',(request_id,)).fetchone()
                if existing:
                    if (existing['tip_id'],existing['stake'],existing['odds'],existing['platform'])!=(tip_id,stake,odds,platform) or json.loads(existing['payload']).get('ledger_contract','manual')!=settlement_mode:raise ValueError('Request ID reused with different bet')
                    return existing['id']
            if c.execute("SELECT id FROM bets WHERE tip_id=? AND request_id LIKE 'auto-stake:%'",(tip_id,)).fetchone():
                raise ValueError('This pick is already auto-tracked. Do not log it again.')
            cursor=c.execute('INSERT INTO bets(tip_id,created_at,stake,odds,platform,payload,request_id) VALUES (?,?,?,?,?,?,?)',(tip_id,utcnow().isoformat(),stake,odds,platform,json.dumps(tip,ensure_ascii=False),request_id))
            bid = cursor.lastrowid
        self.invalidate_held_caches()
        return bid
    def bet(self, bet_id: int):
        with self.db() as c:
            r = c.execute('SELECT * FROM bets WHERE id = ?', (bet_id,)).fetchone()
            if not r: return None
            return {**dict(r), 'payload': json.loads(r['payload'])}
    def bets(self, limit: int = 500):
        with self.db() as c:
            query = 'SELECT * FROM bets ORDER BY id DESC'
            if limit: query += f' LIMIT {limit}'
            rows = c.execute(query).fetchall()
        return [{**dict(r), 'payload': json.loads(r['payload'])} for r in rows]
    def import_reported_bets(self,source_id,rows):
        """Reconcile a user-provided receipt list, preserving duplicate tickets and audit history."""
        signature=hashlib.sha256(json.dumps([{'tip_id':r['tip']['id'],'stake':r['stake'],'odds':r['odds']} for r in rows],sort_keys=True).encode()).hexdigest()
        now=utcnow().isoformat();ids=[]
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            prior=c.execute('SELECT * FROM reported_bet_imports WHERE source_id=?',(source_id,)).fetchone()
            if prior:
                if prior['signature']!=signature:raise ValueError('This source was already imported with different bets')
                return json.loads(prior['bet_ids'])
            for row in rows:
                t=row['tip'];amount=float(row['stake']);odds=float(row['odds'])
                if not(math.isfinite(amount) and amount>0 and math.isfinite(odds) and odds>1):raise ValueError('Invalid reported stake or odds')
                if t.get('reference_platform')!='Stake.com':raise ValueError('A matched Stake contract is required')
                old=c.execute("SELECT * FROM bets WHERE tip_id=? AND status='open' AND json_extract(payload,'$.tracking_source')='auto_assumed_placed' ORDER BY id LIMIT 1",(t['id'],)).fetchone()
                original=json.loads(old['payload']) if old else t
                updated={**original,'ledger_contract':'sportsbook','tracking_source':'user_reported_placed',
                         'execution_verified':False,'odds_basis':'user_reported_accepted_odds',
                         'reported_source':source_id,'reported_at':now,'placement_time_known':False,
                         'tracking_note':'User-provided Stake Active Bets list; accepted odds and stake reported by the user. No direct account verification.'}
                raw=json.dumps(updated,ensure_ascii=False)
                if old:
                    bid=old['id'];c.execute('UPDATE bets SET stake=?,odds=?,payload=? WHERE id=?',(amount,odds,raw,bid))
                else:
                    bid=c.execute('INSERT INTO bets(tip_id,created_at,stake,odds,platform,payload,request_id) VALUES (?,?,?,?,?,?,?)',
                                  (t['id'],now,amount,odds,'Stake.com',raw,f'reported:{source_id}:{len(ids)}')).lastrowid
                after={'stake':amount,'odds':odds,'payload':updated}
                c.execute('INSERT INTO bet_reconciliation(source_id,bet_id,before_record,after_record,created_at) VALUES (?,?,?,?,?)',
                          (source_id,bid,json.dumps(dict(old)) if old else None,json.dumps(after),now))
                c.execute('INSERT OR IGNORE INTO placement_checklist VALUES (?,?)',(t['id'],now))
                ids.append(bid)
            c.execute('INSERT INTO reported_bet_imports VALUES (?,?,?,?)',(source_id,signature,json.dumps(ids),now))
        self.invalidate_held_caches()
        return ids
    def reprice_unplaced(self, request_id, tip_ids, authorization):
        from .unplaced_repricing import reprice_unplaced
        return reprice_unplaced(self, request_id, tip_ids, authorization)

    def open_bets(self):
        with self.db() as c:rows=c.execute("SELECT * FROM bets WHERE status='open' ORDER BY id").fetchall()
        return [{**dict(r),'payload':json.loads(r['payload'])} for r in rows]
    def open_exposure(self):
        with self.db() as c:return float(c.execute("SELECT COALESCE(SUM(stake),0) FROM bets WHERE status='open'").fetchone()[0])
    def settle(self,id,status,pnl,evidence=None):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute('SELECT stake,odds,payload FROM bets WHERE id=?', (id,)).fetchone()
            # A settlement worker may have read its ticket before an explicit
            # unplaced resize. Resolve cash from the locked execution terms.
            if (current and json.loads(current['payload']).get('execution_revision')
                    and evidence and evidence.get('basis')=='verified_score_in_contract_scope'
                    and status in ('won','lost','push')):
                pnl=current['stake']*(current['odds']-1) if status=='won' else (-current['stake'] if status=='lost' else 0.)
            updated=c.execute("UPDATE bets SET status=?,pnl=? WHERE id=? AND status='open'",(status,pnl,id)).rowcount==1
            if updated and evidence:
                tip=json.loads(c.execute('SELECT payload FROM bets WHERE id=?',(id,)).fetchone()['payload'])
                p=tip.get('probability');push=tip.get('push_probability',0);brier=loss=None
                if p is not None and 0<=p<=1-push and status in ('won','lost') and push<1:
                    conditional=p/(1-push);outcome=float(status=='won')
                    brier=(conditional-outcome)**2
                    bounded=min(1-1e-12,max(1e-12,conditional))
                    loss=-(outcome*math.log(bounded)+(1-outcome)*math.log(1-bounded))
                c.execute('INSERT OR IGNORE INTO bet_outcomes VALUES (?,?,?,?,?,?,?,?,?,?)',
                          (id,tip.get('league'),tip.get('market'),tip.get('model_version'),status,p,push,brier,loss,json.dumps(evidence)))
        if updated:
            self.invalidate_held_caches()
        return updated

    def risk_committed(self):
        local=utcnow().astimezone(ZoneInfo(self.settings()['timezone']))
        start=datetime.combine(local.date(),time.min,tzinfo=local.tzinfo).astimezone(timezone.utc).isoformat()
        with self.db() as c:return float(c.execute("SELECT COALESCE(SUM(stake),0) FROM bets WHERE created_at>=? OR status='open'",(start,)).fetchone()[0])
    def held_families(self):
        with self.db() as c:rows=c.execute("SELECT payload FROM bets WHERE status='open'").fetchall()
        return {(t['game_id'],t['market']) for r in rows if (t:=json.loads(r['payload'])) and 'game_id' in t and 'market' in t}
    def _populate_held_caches(self):
        from .contracts import equivalent_contract_ids, allocation_family
        with self.db() as c:
            sd_rows = c.execute('SELECT tip_id, payload FROM system_decisions').fetchall()
            extra_bets = c.execute('SELECT tip_id, payload FROM bets WHERE tip_id NOT IN (SELECT tip_id FROM system_decisions)').fetchall()
        held = set()
        held_alloc = set()
        for r in sd_rows:
            t = json.loads(r['payload'])
            held.add(r['tip_id'])
            if t.get('market') and t.get('id'):
                held.update(equivalent_contract_ids(t))
            if float(t.get('stake', 0)) > 0 and t.get('game_id') and t.get('market'):
                held_alloc.add((t['game_id'], allocation_family(t)))
        for r in extra_bets:
            t = json.loads(r['payload'])
            held.add(r['tip_id'])
            if t.get('market') and t.get('id'):
                held.update(equivalent_contract_ids(t))
            if float(t.get('stake', 0)) > 0 and t.get('game_id') and t.get('market'):
                held_alloc.add((t['game_id'], allocation_family(t)))
        key = str(self.path.resolve())
        Store._held_contracts_cache[key] = held
        Store._held_allocation_families_cache[key] = held_alloc

    def held_contracts(self):
        key = str(self.path.resolve())
        if key not in Store._held_contracts_cache:
            self._populate_held_caches()
        return set(Store._held_contracts_cache[key])
    def held_event_counts(self):
        from collections import Counter
        with self.db() as c:rows=c.execute("SELECT tip_id,payload FROM bets WHERE status='open'").fetchall()
        return dict(Counter(t['game_id'] for t in {r['tip_id']:json.loads(r['payload']) for r in rows}.values() if t.get('game_id')))
    def held_event_stakes(self):
        """Open recorded stake by game for the live $5 exposure cap."""
        with self.db() as c:
            rows=c.execute("SELECT json_extract(payload,'$.game_id') game_id,COALESCE(SUM(stake),0) amount FROM bets WHERE status='open' GROUP BY game_id").fetchall()
        return {r['game_id']:float(r['amount'] or 0.) for r in rows if r['game_id']}
    def held_period_counts(self):
        """Open period/quarter/inning tickets by game."""
        from .contracts import split_market
        with self.db() as c:rows=c.execute("SELECT payload FROM bets WHERE status='open'").fetchall()
        result={}
        for row in rows:
            try:
                tip=json.loads(row['payload']);scope,_=split_market(tip.get('market',''))
                if scope!='full_game' and tip.get('game_id'):
                    result[tip['game_id']]=result.get(tip['game_id'],0)+1
            except (TypeError,ValueError,json.JSONDecodeError):
                continue
        return result
    def daily_family_counts(self):
        """Calendar-day funded first issuances by (league, allocation family)."""
        from .contracts import allocation_family
        settings=self.settings();local=utcnow().astimezone(ZoneInfo(settings['timezone']))
        start=datetime.combine(local.date(),time.min,tzinfo=local.tzinfo).astimezone(timezone.utc).isoformat()
        end=datetime.combine(local.date()+timedelta(days=1),time.min,tzinfo=local.tzinfo).astimezone(timezone.utc).isoformat()
        with self.db() as c:
            rows=c.execute("SELECT i.league,d.payload FROM recommendation_issuance i JOIN system_decisions d ON d.family=i.family WHERE i.issued_at>=? AND i.issued_at<?",(start,end)).fetchall()
        result={}
        for row in rows:
            try:
                tip=json.loads(row['payload']);key=(row['league'],allocation_family(tip));result[key]=result.get(key,0)+1
            except (TypeError,ValueError,json.JSONDecodeError):
                continue
        return result
    def held_allocation_families(self):
        key = str(self.path.resolve())
        if key not in Store._held_allocation_families_cache:
            self._populate_held_caches()
        return set(Store._held_allocation_families_cache[key])
    def held_preference_allocations(self):
        with self.db() as c:
            return {(r['game_id'],r['tier']) for r in c.execute("SELECT DISTINCT json_extract(payload,'$.game_id') game_id,json_extract(payload,'$.funding_basis.tier') tier FROM system_decisions WHERE json_extract(payload,'$.funding_basis.tier') IN ('model_pick','probability_pick') AND json_extract(payload,'$.stake')>0")}
    def settle_forecasts(self,games):
        final={g.id:g for g in games if g.state=='final'}
        with self.db() as c:
            for row in c.execute('SELECT * FROM prospective WHERE outcome IS NULL').fetchall():
                g=final.get(row['game_id'])
                if not g:continue
                # Forecast evaluation shares the exact result-readiness guard used by tickets.
                try:hs,aws,_=settlement_scores({'market':'moneyline'},g)
                except Exception:continue
                try:
                    f=json.loads(row['payload']);outcome=1. if hs>aws else (0. if hs<aws else .5)
                    # Ties excluded from binary Brier; retained for explicit count.
                    draw_p=f.get('draw_probability',0.0) or 0.0
                    home_p=f.get('home_probability',0.0) or 0.0
                    brier=(home_p/(1-draw_p)-outcome)**2 if (outcome!=.5 and draw_p<1.0) else None
                    c.execute('UPDATE prospective SET outcome=?,brier=? WHERE game_id=?',(outcome,brier,g.id))
                except Exception:continue
            for row in c.execute('SELECT * FROM segment_prospective WHERE outcome IS NULL').fetchall():
                g=final.get(row['game_id'])
                if not g:continue
                try:hs,aws,evidence=settlement_scores({'market':row['scope']+':three_way'},g)
                except Exception:continue
                try:
                    f=json.loads(row['payload']);actual='home' if hs>aws else 'away' if hs<aws else 'draw'
                    probs=f.get('model_probabilities') or {}
                    if all(s in probs for s in ('home','draw','away')):
                        brier=sum((probs[s]-int(s==actual))**2 for s in ('home','draw','away'))/3
                    elif all(s in probs for s in ('home','away')):
                        brier=sum((probs[s]-int(s==actual))**2 for s in ('home','away'))/2 if actual in ('home','away') else None
                    elif 'three_way_home' in probs and 'three_way_away' in probs and 'three_way_draw' in probs:
                        brier=((probs['three_way_home']-int(actual=='home'))**2 + (probs['three_way_draw']-int(actual=='draw'))**2 + (probs['three_way_away']-int(actual=='away'))**2)/3
                    elif 'set_winner' in probs and actual in ('home','away'):
                        brier=(probs['set_winner']-int(actual=='home'))**2
                    else:
                        brier=None
                    outcome={'home':hs,'away':aws,'evidence':evidence}
                    c.execute('UPDATE segment_prospective SET outcome=?,brier=? WHERE game_id=? AND scope=?',(json.dumps(outcome),brier,g.id,row['scope']))
                except Exception:continue
        self.invalidate_held_caches()
    def performance(self):
        try:
            from .in_memory_cache import GLOBAL_IN_MEMORY_CACHE
            cache_key = f"store_performance:{self.path.resolve()}"
            return GLOBAL_IN_MEMORY_CACHE.get_or_compute(cache_key, self._compute_performance, ttl_seconds=15.0)
        except Exception:
            return self._compute_performance()

    def _compute_performance(self):
        with self.db() as c:
            owned=dict(c.execute("SELECT COUNT(*) count,COALESCE(SUM(status!='open'),0) settled,COALESCE(SUM(status='won'),0) won,COALESCE(SUM(status='lost'),0) lost,COALESCE(SUM(status='push'),0) pushes,COALESCE(SUM(CASE WHEN status!='open' THEN json_extract(payload,'$.stake') ELSE 0 END),0) turnover,COALESCE(SUM(pnl),0) pnl FROM system_decisions").fetchone())
            funding_tiers=[dict(r) for r in c.execute("""SELECT
                COALESCE(json_extract(payload,'$.funding_basis.tier'),'legacy_unspecified') tier,
                json_extract(payload,'$.league') league,json_extract(payload,'$.market') market,
                COUNT(*) issued,COUNT(DISTINCT json_extract(payload,'$.game_id')) games,
                SUM(status!='open') settled,SUM(status='won') won,SUM(status='lost') lost,SUM(status='push') pushes,
                COALESCE(SUM(CASE WHEN status!='open' THEN json_extract(payload,'$.stake') ELSE 0 END),0) settled_stake,
                COALESCE(SUM(pnl),0) pnl,MIN(issued_at) first_issued_at,MAX(issued_at) last_issued_at
                FROM system_decisions GROUP BY tier,league,market ORDER BY tier,league,market""")]
            bets=dict(c.execute("SELECT COUNT(*) count,COALESCE(SUM(status!='open'),0) settled,COALESCE(SUM(status='won'),0) won,COALESCE(SUM(status='lost'),0) lost,COALESCE(SUM(status='push'),0) pushes,COALESCE(SUM(CASE WHEN status!='open' THEN stake ELSE 0 END),0) turnover,COALESCE(SUM(pnl),0) pnl,MIN(created_at) since FROM bets").fetchone())
            cutoff_48h=(utcnow()-timedelta(hours=48)).isoformat()
            bets_48h=dict(c.execute("""SELECT COUNT(*) count,COALESCE(SUM(b.status!='open'),0) settled,COALESCE(SUM(b.status='won'),0) won,COALESCE(SUM(b.status='lost'),0) lost,COALESCE(SUM(b.status='push'),0) pushes,COALESCE(SUM(b.stake),0) turnover,COALESCE(SUM(b.pnl),0) pnl FROM bets b LEFT JOIN bet_outcomes o ON o.bet_id=b.id WHERE b.status!='open' AND COALESCE(json_extract(o.evidence,'$.settled_at'),b.created_at)>=?""",(cutoff_48h,)).fetchone())
            league_rows_48h=[dict(r) for r in c.execute("""SELECT json_extract(b.payload,'$.league') league,COUNT(*) count,COALESCE(SUM(b.status!='open'),0) settled,COALESCE(SUM(b.status='won'),0) won,COALESCE(SUM(b.status='lost'),0) lost,COALESCE(SUM(b.status='push'),0) pushes,COALESCE(SUM(b.stake),0) turnover,COALESCE(SUM(b.pnl),0) pnl FROM bets b LEFT JOIN bet_outcomes o ON o.bet_id=b.id WHERE b.status!='open' AND COALESCE(json_extract(o.evidence,'$.settled_at'),b.created_at)>=? GROUP BY json_extract(b.payload,'$.league')""",(cutoff_48h,)).fetchall()]
            league_rows_total=[dict(r) for r in c.execute("""SELECT json_extract(b.payload,'$.league') league,COUNT(*) count,COALESCE(SUM(b.status!='open'),0) settled,COALESCE(SUM(b.status='won'),0) won,COALESCE(SUM(b.status='lost'),0) lost,COALESCE(SUM(b.status='push'),0) pushes,COALESCE(SUM(b.stake),0) turnover,COALESCE(SUM(b.pnl),0) pnl FROM bets b WHERE b.status!='open' GROUP BY json_extract(b.payload,'$.league')""").fetchall()]
            summary="SELECT COUNT(*) count,COALESCE(SUM(status!='open'),0) settled,COALESCE(SUM(CASE WHEN status!='open' THEN stake ELSE 0 END),0) turnover,COALESCE(SUM(pnl),0) pnl FROM bets WHERE "
            auto="COALESCE(json_extract(payload,'$.tracking_source'),'manual')='auto_assumed_placed'"
            assumed=dict(c.execute(summary+auto).fetchone())
            confirmed=dict(c.execute(summary+'NOT ('+auto+')').fetchone())
            selections=[dict(r) for r in c.execute("SELECT league,market,model_version,COUNT(*) settled,SUM(result='won') won,SUM(result='lost') lost,SUM(result='push') pushes,AVG(brier) brier,AVG(log_loss) log_loss FROM bet_outcomes WHERE bet_id IN (SELECT MIN(o.bet_id) FROM bet_outcomes o JOIN bets b ON b.id=o.bet_id GROUP BY b.tip_id) GROUP BY league,market,model_version")]
            prospect=[dict(r) for r in c.execute('SELECT league,COUNT(*) published,SUM(CASE WHEN outcome IS NOT NULL THEN 1 ELSE 0 END) settled,AVG(brier) brier FROM prospective GROUP BY league')]
            segment_prospect=[dict(r) for r in c.execute('SELECT scope,COUNT(*) published,SUM(outcome IS NOT NULL) settled,AVG(brier) brier FROM segment_prospective GROUP BY scope')]
            first_dec=c.execute('SELECT MIN(issued_at) value FROM system_decisions').fetchone()
            first=first_dec['value'] if first_dec and first_dec['value'] else c.execute('SELECT MIN(created_at) value FROM outbox').fetchone()['value']
        cost=self.settings().get('monthly_system_cost');days=max(0,(utcnow()-parse_time(first)).total_seconds()/86400) if first else 0.
        accrued=cost*days/(365.2425/12) if cost is not None else None
        owned.update(roi=owned['pnl']/owned['turnover'] if owned['turnover'] else None,net_after_system_cost=owned['pnl']-accrued if accrued is not None else None,basis='Original funded decisions at issued odds, plus explicitly reported legacy displayed selections at reported ticket terms. Losses retained; duplicate tickets remain separate in the execution ledger.')
        def _calc_p(d):
            w,l,to,p=d.get('won',0),d.get('lost',0),d.get('turnover',0),d.get('pnl',0)
            d['hit_rate']=w/(w+l) if (w+l) else None
            d['roi']=p/to if to else None
            return d
        bets=_calc_p(bets)
        bets_48h=_calc_p(bets_48h)
        bets['by_league']={r['league']:_calc_p(r) for r in league_rows_total if r.get('league')}
        bets_48h['by_league']={r['league']:_calc_p(r) for r in league_rows_48h if r.get('league')}
        rolling_48h=bets_48h
        from .economics import economic_evidence
        economics=economic_evidence(self.system_decisions(),accrued_system_cost=accrued,timezone=self.settings()['timezone'])
        for row in funding_tiers:
            row['roi']=row['pnl']/row['settled_stake'] if row['settled_stake'] else None
            row['hit_rate']=row['won']/(row['won']+row['lost']) if row['won']+row['lost'] else None
        return {'funding_tiers':funding_tiers,'funding_tier_basis':'Original immutable issued policy, league and market; no retrospective tier reassignment. P&L after stakes, before operating costs. Related game decisions are not independent samples. Legacy records without an explicit funding tier remain unspecified.','economic_evidence':economics,'system_decisions':owned,'actual_bets':confirmed,'ledger_bets':bets,'rolling_48h':rolling_48h,'assumed_bets':assumed,'selection_outcomes':selections,'prospective':prospect,'segment_prospective':segment_prospect,'monthly_system_cost':cost,'estimated_accrued_system_cost':accrued,'net_after_system_cost':bets['pnl']-accrued if accrued is not None else None,'cost_period_from':first,'cost_period_days':days,'actual_roi':confirmed['pnl']/confirmed['turnover'] if confirmed['turnover'] else None,'ledger_roi':bets['pnl']/bets['turnover'] if bets['turnover'] else None,'note':'Auto-tracked bets follow your standing instruction and use recommendation odds and stakes. Their P&L is estimated, not verified against Stake account transactions. Selection scores use frozen pregame probabilities and final sports results; pushes are excluded from conditional Brier and log loss.'}
