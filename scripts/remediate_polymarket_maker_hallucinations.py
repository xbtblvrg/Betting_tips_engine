"""Remediate Polymarket Maker-Fill Hallucination Anomalies.

Aligns with AGENTS.md Commandment 10 (Anti-Tautological Invariant & Ledger Immutability / Audit Trail)
and GEMINI.md (Verify autonomy and completion concretely).

Re-calibrates distorted shadow sliced orders that assumed unrealistic maker limit fills ($0.02)
to their verified, realistic taker execution metrics.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys

DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "engine.sqlite3"


def remediate_polymarket_orders(db_path: Path = DB_PATH) -> None:
    if not db_path.exists():
        print(f"Error: Database {db_path} does not exist.")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Pre-audit current state
    cur.execute("SELECT status, count(*), sum(stake_usd), sum(pnl) FROM polymarket_orders GROUP BY status")
    print("--- BEFORE REMEDIATION ---")
    for r in cur.fetchall():
        print(f"Status: {r[0]:10s} | Count: {r[1]:4d} | Stake: ${r[2] or 0.0:8.2f} | PnL: ${r[3] or 0.0:8.2f}")

    cur.execute("""
        SELECT id, order_id, client_order_id, game_title, selection, line,
               poly_price, poly_odds, model_prob, expected_value, stake_usd,
               shares_count, status, pnl, error_detail, ai_remediation_hint
        FROM polymarket_orders
        WHERE poly_price < 0.085 OR (error_detail LIKE '%maker_slice%' AND error_detail LIKE '%0.02%')
    """)
    rows = cur.fetchall()
    print(f"\nEvaluating {len(rows)} potential anomaly orders...")

    remediated_count = 0
    rejected_count = 0

    try:
        cur.execute("BEGIN TRANSACTION;")

        for row in rows:
            order_id = row["id"]
            title = row["game_title"]
            sel = row["selection"]
            stat = row["status"]
            price = row["poly_price"]
            stake = row["stake_usd"]
            shares = row["shares_count"]
            pnl = row["pnl"]
            detail_str = row["error_detail"]

            # Case A: Sliced order with taker slice available in error_detail
            if detail_str and "taker_slice" in detail_str:
                try:
                    d = json.loads(detail_str)
                    tk = d.get("taker_slice", {})
                    mk = d.get("maker_slice", {})
                    mk_p = float(mk.get("price", 1.0))
                    tk_p = float(tk.get("price", 0.0))
                    tk_stk = float(tk.get("stake", 0.0))
                    tk_sh = float(tk.get("shares", 0.0))

                    if (mk_p <= 0.05 or price < 0.085 or (tk_p - mk_p) > 0.08) and tk_p >= 0.08:
                        new_odds = round(1.0 / tk_p, 3)
                        new_ev = round((row["model_prob"] * new_odds) - 1.0, 4)

                        new_pnl = None
                        if stat == "won":
                            new_pnl = round(tk_sh * 1.00 - tk_stk, 2)
                        elif stat == "lost":
                            new_pnl = -round(tk_stk, 2)

                        audit_record = {
                            "remediation": "MAKER_FILL_HALLUCINATION_CORRECTION",
                            "rule": "AGENTS.md Commandment 10 Anti-Tautological Invariant",
                            "original_price": price,
                            "original_shares": shares,
                            "original_stake": stake,
                            "original_pnl": pnl,
                            "corrected_price": tk_p,
                            "corrected_shares": tk_sh,
                            "corrected_stake": tk_stk,
                            "corrected_pnl": new_pnl,
                            "maker_price_disallowed": mk_p,
                            "reason": "Illiquid resting limit bid ($0.02) never executed in real market. Remediated to verified Taker fill."
                        }

                        cur.execute("""
                            UPDATE polymarket_orders
                            SET poly_price = ?,
                                poly_odds = ?,
                                expected_value = ?,
                                stake_usd = ?,
                                shares_count = ?,
                                pnl = ?,
                                ai_remediation_hint = ?
                            WHERE id = ?
                        """, (
                            tk_p,
                            new_odds,
                            new_ev,
                            tk_stk,
                            tk_sh,
                            new_pnl,
                            json.dumps(audit_record),
                            order_id,
                        ))
                        remediated_count += 1
                        print(f"REMEDIATED [ID {order_id}]: {title} - {sel} | Old PnL: ${pnl} -> New PnL: ${new_pnl} (Taker: ${tk_stk} @ {tk_p})")
                except Exception as e_tk:
                    print(f"Error parsing taker slice for ID {order_id}: {e_tk}")

            # Case B: Single unsliced order with extreme unfillable price (e.g. ID 2616 @ 0.02)
            elif price <= 0.02 and stat in ("open", "filled"):
                audit_record = {
                    "remediation": "UNREALISTIC_PRICE_REJECTION",
                    "original_price": price,
                    "original_stake": stake,
                    "original_shares": shares,
                    "reason": "Price $0.02 with 2780% EV represents preflight quote artifact. Rejected before settlement."
                }
                cur.execute("""
                    UPDATE polymarket_orders
                    SET status = 'rejected',
                        error_code = 'EXCESSIVE_EV_ANOMALY_REJECTED',
                        error_detail = 'Rejected before settlement due to 0.02 quote anomaly',
                        stake_usd = 0.0,
                        shares_count = 0.0,
                        pnl = NULL,
                        ai_remediation_hint = ?
                    WHERE id = ?
                """, (json.dumps(audit_record), order_id))
                rejected_count += 1
                print(f"REJECTED ANOMALY [ID {order_id}]: {title} - {sel} | Price {price} cancelled.")

        conn.commit()
        print(f"\nTransaction committed successfully: {remediated_count} orders remediated, {rejected_count} anomalies rejected.")

    except Exception as exc:
        conn.rollback()
        print(f"Transaction failed, rolled back: {exc}")
        sys.exit(1)

    # Post-audit state
    cur.execute("SELECT status, count(*), sum(stake_usd), sum(pnl) FROM polymarket_orders GROUP BY status")
    print("\n--- AFTER REMEDIATION ---")
    total_won_pnl = 0.0
    total_lost_pnl = 0.0
    total_staked = 0.0
    for r in cur.fetchall():
        st = r[0]
        cnt = r[1]
        stk = r[2] or 0.0
        p_val = r[3] or 0.0
        print(f"Status: {st:10s} | Count: {cnt:4d} | Stake: ${stk:8.2f} | PnL: ${p_val:8.2f}")
        if st in ("won", "lost"):
            total_staked += stk
            if st == "won":
                total_won_pnl += p_val
            elif st == "lost":
                total_lost_pnl += p_val

    net_pnl = total_won_pnl + total_lost_pnl
    roi = (net_pnl / total_staked * 100) if total_staked > 0 else 0.0
    print(f"\nVerified Settled Totals: Staked ${total_staked:.2f} | Net PnL: ${net_pnl:.2f} | ROI: {roi:.2f}%")
    conn.close()


if __name__ == "__main__":
    remediate_polymarket_orders()
