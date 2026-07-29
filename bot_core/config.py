"""
Single place that decides which ExecutionAdapter gets instantiated. Nothing
else in the codebase should hard-code a broker name — this is the one file
that changes if we migrate from IBKR to Tiger/Moomoo/Schwab per ADR-001.
"""

import os

BROKER = os.environ.get("TRADING_BOT_BROKER", "ibkr")  # ibkr | tiger | moomoo


def get_execution_adapter():
    if BROKER == "ibkr":
        from execution.ibkr_adapter import IBKRAdapter
        account_id = os.environ["IBKR_ACCOUNT_ID"]
        return IBKRAdapter(account_id=account_id)
    elif BROKER == "tiger":
        raise NotImplementedError(
            "Tiger adapter not yet built — candidate per ADR-002, pending "
            "a paper-account test of the RSA auth flow's real automation "
            "friction before committing engineering time here."
        )
    elif BROKER == "moomoo":
        raise NotImplementedError(
            "Moomoo adapter not yet built — same architectural friction "
            "class as IBKR (persistent OpenD gateway), lower priority "
            "than confirming Tiger's auth model first."
        )
    else:
        raise ValueError(f"Unknown broker: {BROKER}")
