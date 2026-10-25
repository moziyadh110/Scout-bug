"""One-time devnet setup: wallets, devnet SOL for fees, the 'test USDC' mint, demo balances."""

import json
from pathlib import Path
from typing import Any

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import CreateAccountParams, TransferParams, create_account, transfer
from solders.transaction import VersionedTransaction
from spl.token.constants import MINT_LEN, TOKEN_PROGRAM_ID
from spl.token.instructions import (
    create_idempotent_associated_token_account,
    get_associated_token_address,
    initialize_mint,
    mint_to_checked,
)
from spl.token.models import InitializeMintParams, MintToCheckedParams

from scout.adapters.cloud.solana_ledger import _run, load_keypair
from scout.config import Settings

DECIMALS = 6
FUNDER_START = 1_000 * 10**DECIMALS  # 1000 test USDC
MIN_SOL = 200_000_000  # 0.2 SOL covers many devnet fees
AIRDROP = 1_000_000_000
ESCROW_SOL = 100_000_000  # 0.1 SOL of fees for releases, refunds and memos


def _keypair(path: Path) -> Keypair:
    if path.exists():
        return load_keypair(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    kp = Keypair()
    path.write_text(json.dumps(list(bytes(kp))))
    return kp


def register_wallet(s: Settings, login: str, address: str) -> None:
    Pubkey.from_string(address)  # validates
    path = s.scout_data_dir / "wallets.json"
    wallets = json.loads(path.read_text()) if path.exists() else {}
    wallets[login] = address
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(wallets, indent=1))


def setup(s: Settings) -> dict[str, Any]:
    keys = s.solana_escrow_keypair.parent
    escrow, funder = _keypair(s.solana_escrow_keypair), _keypair(s.solana_funder_keypair)
    mint = _keypair(keys / "mint.keypair.json")
    contributor = _keypair(keys / "contributor-newbie.keypair.json")  # demo payout wallet
    log: list[str] = []

    async def send(c: AsyncClient, payer: Keypair, signers: list[Keypair], ixs: list[Any]) -> str:
        bh = (await c.get_latest_blockhash(Confirmed)).value.blockhash
        tx = VersionedTransaction(MessageV0.try_compile(payer.pubkey(), ixs, [], bh), signers)
        sig = (await c.send_transaction(tx)).value
        await c.confirm_transaction(sig, Confirmed)
        return str(sig)

    async def go() -> None:
        async with AsyncClient(s.solana_rpc_url) as c:
            # Only the funder needs faucet SOL; it forwards the escrow's fee budget.
            if (await c.get_balance(funder.pubkey(), Confirmed)).value < MIN_SOL + ESCROW_SOL:
                try:
                    asig = (await c.request_airdrop(funder.pubkey(), AIRDROP, Confirmed)).value
                    await c.confirm_transaction(asig, Confirmed)
                    log.append("airdropped 1 SOL to the funder")
                except Exception as e:
                    raise RuntimeError(
                        f"devnet faucet refused ({type(e).__name__}). Send 1 devnet SOL to the "
                        f"funder {funder.pubkey()} at https://faucet.solana.com, then run "
                        "`uv run scout solana-setup` again."
                    ) from e
            if (await c.get_balance(escrow.pubkey(), Confirmed)).value < ESCROW_SOL // 2:
                await send(
                    c,
                    funder,
                    [funder],
                    [
                        transfer(
                            TransferParams(
                                from_pubkey=funder.pubkey(),
                                to_pubkey=escrow.pubkey(),
                                lamports=ESCROW_SOL,
                            )
                        )
                    ],
                )
                log.append("sent 0.1 SOL to the escrow for fees")
            if (await c.get_account_info(mint.pubkey(), Confirmed)).value is None:
                rent = (await c.get_minimum_balance_for_rent_exemption(MINT_LEN)).value
                sig = await send(
                    c,
                    funder,
                    [funder, mint],
                    [
                        create_account(
                            CreateAccountParams(
                                from_pubkey=funder.pubkey(),
                                to_pubkey=mint.pubkey(),
                                lamports=rent,
                                space=MINT_LEN,
                                owner=TOKEN_PROGRAM_ID,
                            )
                        ),
                        initialize_mint(
                            InitializeMintParams(
                                decimals=DECIMALS,
                                program_id=TOKEN_PROGRAM_ID,
                                mint=mint.pubkey(),
                                mint_authority=funder.pubkey(),
                            )
                        ),
                    ],
                )
                log.append(f"created test USDC mint {mint.pubkey()} ({sig})")
            ixs = [
                create_idempotent_associated_token_account(funder.pubkey(), o, mint.pubkey())
                for o in (funder.pubkey(), escrow.pubkey())
            ]
            await send(c, funder, [funder], ixs)
            ata = get_associated_token_address(funder.pubkey(), mint.pubkey())
            if int((await c.get_token_account_balance(ata, Confirmed)).value.amount) == 0:
                sig = await send(
                    c,
                    funder,
                    [funder],
                    [
                        mint_to_checked(
                            MintToCheckedParams(
                                program_id=TOKEN_PROGRAM_ID,
                                mint=mint.pubkey(),
                                dest=ata,
                                mint_authority=funder.pubkey(),
                                amount=FUNDER_START,
                                decimals=DECIMALS,
                            )
                        )
                    ],
                )
                log.append(f"minted 1000 test USDC to the funder ({sig})")

    _run(go())
    info = {
        "cluster": "devnet",
        "mint": str(mint.pubkey()),
        "decimals": DECIMALS,
        "escrow": str(escrow.pubkey()),
        "funder": str(funder.pubkey()),
        "demo_contributor": str(contributor.pubkey()),
    }
    (keys / "solana.json").write_text(json.dumps(info, indent=1))
    register_wallet(s, "newbie", str(contributor.pubkey()))
    return {**info, "log": log}
