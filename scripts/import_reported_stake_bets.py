"""Reconcile an explicit user-provided Stake receipt list; never places orders."""
import argparse
import json
import sqlite3
from pathlib import Path

from betting_engine.storage import Store
from betting_engine.snapshots import decode_publication
from betting_engine.domain import utcnow
from betting_engine.presentation import selection_name

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('file',type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();report=json.loads(args.file.read_text());store=Store()
    with store.db() as c:
        snapshot=c.execute('SELECT id,payload FROM publications WHERE created_at<=? ORDER BY id DESC LIMIT 1',(report['snapshot_before'],)).fetchone()
    if not snapshot:raise ValueError('No publication before the reported cutoff')
    publication=decode_publication(snapshot['payload'])
    tips={t['id']:t for t in publication.get('recommendations',[])+publication.get('alternatives',[])}
    rows=[]
    for receipt in report['bets']:
        tip=tips.get(receipt['tip_id'])
        if not tip or selection_name(tip)!=receipt['selection']:raise ValueError('Receipt does not match the frozen published selection')
        tip={**tip,'reported_currency':report['currency'],'reported_snapshot_id':snapshot['id']}
        rows.append({'tip':tip,'stake':receipt['stake'],'odds':receipt['odds']})
    print(json.dumps({'source':report['source_id'],'snapshot_id':snapshot['id'],'tickets':len(rows),'unique_selections':len({r['tip']['id'] for r in rows}),'total_stake':sum(r['stake'] for r in rows)}))
    if args.apply:
        backup=store.path.parent/'backups'/('before-receipt-import-'+utcnow().strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3');backup.parent.mkdir(exist_ok=True)
        with sqlite3.connect(store.path) as src,sqlite3.connect(backup) as dest:src.backup(dest)
        ids=store.import_reported_bets(report['source_id'],rows)
        print(json.dumps({'bet_ids':ids,'ledger_count':len(store.bets()),'open_stake':store.open_exposure(),'backup':str(backup)}))

if __name__=='__main__':main()
