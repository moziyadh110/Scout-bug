import json

import pytest

from scout.adapters.cloud.solana_setup import register_wallet


def test_register_wallet_validates_addresses(settings):
    register_wallet(settings, "newbie", "EmTfwxXBT4avHZvS87nzMuer9UJdW4yVCo1qddd44k1g")
    data = json.loads((settings.scout_data_dir / "wallets.json").read_text())
    assert data == {"newbie": "EmTfwxXBT4avHZvS87nzMuer9UJdW4yVCo1qddd44k1g"}
    with pytest.raises(ValueError):
        register_wallet(settings, "mallory", "not-a-solana-address")
