import sqlite3
import threading
import uuid
from pathlib import Path

ESCROW = "escrow"
TREASURY = "treasury"

_SCHEMA = """
create table if not exists accounts (name text primary key, balance integer not null);
create table if not exists txs (
    id text primary key, key text unique not null, kind text not null,
    src text, dst text, amount integer not null, memo text, ts text default current_timestamp
);
"""


class SqliteLedger:
    """Mock ledger: balances, escrow and a memo registry. Same contract as the Solana adapter."""

    def __init__(self, path: Path, faucet_balance: int = 1_000_000_000) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.faucet_balance = faucet_balance
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _transfer(
        self, key: str, kind: str, src: str | None, dst: str | None, amount: int, memo: str = ""
    ) -> str:
        if amount < 0:
            raise ValueError("negative amount")
        with self._lock, self._conn() as c:
            row = c.execute("select id from txs where key = ?", (key,)).fetchone()
            if row:  # idempotent replay
                return str(row[0])
            for acct in (src, dst):
                if acct:
                    start = self.faucet_balance if acct not in (ESCROW, TREASURY) else 0
                    c.execute("insert or ignore into accounts values (?, ?)", (acct, start))
            if src:
                (bal,) = c.execute("select balance from accounts where name = ?", (src,)).fetchone()
                if bal < amount:
                    raise ValueError(f"insufficient funds in {src}: {bal} < {amount}")
                c.execute("update accounts set balance = balance - ? where name = ?", (amount, src))
            if dst:
                c.execute("update accounts set balance = balance + ? where name = ?", (amount, dst))
            tx = f"mock-{uuid.uuid4().hex[:16]}"
            c.execute(
                "insert into txs (id, key, kind, src, dst, amount, memo) values (?,?,?,?,?,?,?)",
                (tx, key, kind, src, dst, amount, memo),
            )
            return tx

    def lock(self, key: str, payer: str, amount: int) -> str:
        return self._transfer(key, "lock", payer, ESCROW, amount)

    def release(self, key: str, amount: int, memo: str) -> str:
        return self._transfer(key, "release", ESCROW, TREASURY, amount, memo)

    def refund(self, key: str, payer: str, amount: int, memo: str) -> str:
        return self._transfer(key, "refund", ESCROW, payer, amount, memo)

    def record(self, key: str, memo: str) -> str:
        return self._transfer(key, "record", None, None, 0, memo)

    def get_memo(self, ref: str) -> str | None:
        with self._conn() as c:
            row = c.execute("select memo from txs where id = ?", (ref,)).fetchone()
        return row[0] if row else None

    def balance(self, account: str) -> int:
        with self._conn() as c:
            row = c.execute("select balance from accounts where name = ?", (account,)).fetchone()
        if row:
            return int(row[0])
        return self.faucet_balance if account not in (ESCROW, TREASURY) else 0

    def explorer_url(self, ref: str) -> str | None:
        return None

    def payer_account(self) -> str:
        return "alice"
