"""Ledger port on Solana devnet: test-USDC (SPL token) escrow plus a Memo-program registry.

INTERIM DESIGN: escrow is a token account owned by a backend-held keypair, not a
non-custodial on-chain program. Only deterministic code in scout/bounty.py calls release().
Keypairs live in keys/ (gitignored); no model ever sees them.
"""

import asyncio
import json
import re
import threading
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.instruction import Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction
from solders.transaction_status import TransactionConfirmationStatus as Status
from spl.memo.constants import MEMO_PROGRAM_ID
from spl.memo.instructions import create_memo
from spl.memo.models import MemoParams
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import (
    create_idempotent_associated_token_account,
    get_associated_token_address,
    transfer_checked,
)
from spl.token.models import TransferCheckedParams

from scout.config import Settings

EXPLORER = "https://explorer.solana.com/tx/{sig}?cluster=devnet"
MEMO_LOG = re.compile(r'Memo \(len \d+\): (".*")$')


def load_keypair(path: Path) -> Keypair:
    return Keypair.from_bytes(bytes(json.loads(path.read_text())))


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run async RPC from sync code, even when called inside an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # ponytail: blocks the caller's loop for one RPC round trip; fine for this app's traffic.
    with ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, coro).result()


async def confirm(c: AsyncClient, sig: Signature, timeout: float = 90.0) -> None:
    """Wait for 'confirmed', polling gently; public devnet RPC rate-limits fast polling."""
    delay, waited = 1.0, 0.0
    while waited < timeout:
        try:
            st = (await c.get_signature_statuses([sig])).value[0]
        except Exception:  # transient RPC error (often HTTP 429): back off and retry
            st = None
        if st is not None:
            if st.err is not None:
                raise RuntimeError(f"transaction {sig} failed: {st.err}")
            if st.confirmation_status in (Status.Confirmed, Status.Finalized):
                return
        await asyncio.sleep(delay)
        waited += delay
        delay = min(delay * 1.5, 5.0)
    raise TimeoutError(f"transaction {sig} not confirmed after {timeout:.0f}s")


class SolanaLedger:
    def __init__(self, s: Settings) -> None:
        info_path = s.solana_escrow_keypair.parent / "solana.json"
        if not (s.solana_escrow_keypair.exists() and info_path.exists()):
            raise RuntimeError("Solana is not set up: run `uv run scout solana-setup` first")
        self.url = s.solana_rpc_url
        self.escrow = load_keypair(s.solana_escrow_keypair)
        self.funder = load_keypair(s.solana_funder_keypair)
        info = json.loads(info_path.read_text())
        self.mint = Pubkey.from_string(info["mint"])
        self.decimals = int(info["decimals"])
        self.cap = s.bounty_cap_units
        self.txs_path = s.scout_data_dir / "solana_txs.json"  # idempotency key -> signature
        self.wallets_path = s.scout_data_dir / "wallets.json"  # GitHub login -> payout address
        self._lock = threading.Lock()

    # --- plumbing ----------------------------------------------------------------------

    def _ata(self, owner: Pubkey) -> Pubkey:
        return get_associated_token_address(owner, self.mint)

    def _transfer(self, src_owner: Pubkey, dst_owner: Pubkey, amount: int) -> Instruction:
        return transfer_checked(
            TransferCheckedParams(
                program_id=TOKEN_PROGRAM_ID,
                source=self._ata(src_owner),
                mint=self.mint,
                dest=self._ata(dst_owner),
                owner=src_owner,
                amount=amount,
                decimals=self.decimals,
            )
        )

    def _memo(self, signer: Keypair, text: str) -> Instruction:
        return create_memo(
            MemoParams(program_id=MEMO_PROGRAM_ID, signer=signer.pubkey(), message=text.encode())
        )

    def _send(
        self, key: str, payer: Keypair, signers: list[Keypair], ixs: list[Instruction]
    ) -> str:
        with self._lock:
            done = json.loads(self.txs_path.read_text()) if self.txs_path.exists() else {}
            if key in done:  # idempotent replay: never send the same payment twice
                return str(done[key])

            async def go() -> str:
                async with AsyncClient(self.url) as c:
                    bh = (await c.get_latest_blockhash(Confirmed)).value.blockhash
                    msg = MessageV0.try_compile(payer.pubkey(), ixs, [], bh)
                    tx = VersionedTransaction(msg, [payer, *[k for k in signers if k != payer]])
                    sig = (await c.send_transaction(tx)).value
                    await confirm(c, sig)
                    return str(sig)

            sig = _run(go())
            done[key] = sig
            self.txs_path.parent.mkdir(parents=True, exist_ok=True)
            self.txs_path.write_text(json.dumps(done, indent=1))
            return sig

    def _check_amount(self, amount: int) -> None:
        if not 0 < amount <= self.cap:  # the cap is enforced here too, not only in bounty.py
            raise ValueError(f"amount {amount} outside 1..{self.cap}")

    # --- Ledger port -------------------------------------------------------------------

    def fund(self, key: str, funder: str, amount: int) -> str:
        if funder != str(self.funder.pubkey()):
            raise ValueError("this build can only sign for the demo funder wallet")
        self._check_amount(amount)
        ixs = [
            self._transfer(self.funder.pubkey(), self.escrow.pubkey(), amount),
            self._memo(self.funder, f"scout:fund:{key}"),
        ]
        return self._send(key, self.funder, [self.funder], ixs)

    def _payout(self, key: str, to: str, amount: int, memo: str) -> str:
        self._check_amount(amount)
        dst = Pubkey.from_string(to)
        ixs = [
            create_idempotent_associated_token_account(self.escrow.pubkey(), dst, self.mint),
            self._transfer(self.escrow.pubkey(), dst, amount),
            self._memo(self.escrow, memo),
        ]
        return self._send(key, self.escrow, [self.escrow], ixs)

    def release(self, key: str, to: str, amount: int, memo: str) -> str:
        return self._payout(key, to, amount, memo)

    def refund(self, key: str, to: str, amount: int, memo: str) -> str:
        return self._payout(key, to, amount, memo)

    def record(self, key: str, memo: str) -> str:
        return self._send(key, self.escrow, [self.escrow], [self._memo(self.escrow, memo)])

    def get_memo(self, ref: str) -> str | None:
        async def go() -> list[str]:
            async with AsyncClient(self.url) as c:
                r = await c.get_transaction(Signature.from_string(ref), "json", Confirmed, 0)
                meta = r.value.transaction.meta if r.value else None
                return list(meta.log_messages or []) if meta else []

        for line in _run(go()):
            if m := MEMO_LOG.search(line):
                return str(json.loads(m.group(1)))
        return None

    def balance(self, account: str) -> int:
        owner = self.escrow.pubkey() if account == "escrow" else Pubkey.from_string(account)

        async def go() -> int:
            async with AsyncClient(self.url) as c:
                try:
                    r = await c.get_token_account_balance(self._ata(owner), Confirmed)
                except Exception:  # no token account yet
                    return 0
                return int(r.value.amount)

        return _run(go())

    def explorer_url(self, ref: str) -> str | None:
        return EXPLORER.format(sig=ref)

    def funder_account(self) -> str:
        return str(self.funder.pubkey())

    def payee_account(self, github_login: str) -> str:
        wallets = json.loads(self.wallets_path.read_text()) if self.wallets_path.exists() else {}
        if github_login not in wallets:
            raise LookupError(f"no payout wallet registered for {github_login}")
        return str(wallets[github_login])
