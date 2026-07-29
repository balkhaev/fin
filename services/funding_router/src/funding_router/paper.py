from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass

from .config import Settings
from .models import Candidate, MarketSnapshot, now_ms
from .store import SQLiteStore


@dataclass(slots=True)
class PaperPosition:
    position_id: str
    candidate: dict
    opened_at_ms: int
    updated_at_ms: int
    base_amount: float
    long_next_funding_ms: int
    short_next_funding_ms: int
    long_interval_ms: int
    short_interval_ms: int
    funding_pnl_usdt: float
    mark_pnl_usdt: float
    charged_costs_usdt: float
    funding_events: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> PaperPosition:
        return cls(
            position_id=str(payload["position_id"]),
            candidate=dict(payload["candidate"]),
            opened_at_ms=int(payload["opened_at_ms"]),
            updated_at_ms=int(payload["updated_at_ms"]),
            base_amount=float(payload["base_amount"]),
            long_next_funding_ms=int(payload["long_next_funding_ms"]),
            short_next_funding_ms=int(payload["short_next_funding_ms"]),
            long_interval_ms=int(payload["long_interval_ms"]),
            short_interval_ms=int(payload["short_interval_ms"]),
            funding_pnl_usdt=float(payload["funding_pnl_usdt"]),
            mark_pnl_usdt=float(payload.get("mark_pnl_usdt", 0.0)),
            charged_costs_usdt=float(payload["charged_costs_usdt"]),
            funding_events=int(payload["funding_events"]),
        )

    @property
    def net_pnl_usdt(self) -> float:
        return self.funding_pnl_usdt + self.mark_pnl_usdt - self.charged_costs_usdt


class PaperTrader:
    POSITION_KEY = "paper.position"
    ACCOUNT_KEY = "paper.account"

    def __init__(self, settings: Settings, store: SQLiteStore):
        self.settings = settings
        self.store = store
        raw = store.get_state(self.POSITION_KEY)
        self.position = PaperPosition.from_dict(raw) if raw else None
        account = store.get_state(self.ACCOUNT_KEY)
        self.realized_pnl_usdt = (
            float(account.get("realized_pnl_usdt", 0.0)) if account else 0.0
        )
        self.closed_positions = (
            int(account.get("closed_positions", 0)) if account else 0
        )

    def _persist(self) -> None:
        if self.position is None:
            self.store.set_state(self.POSITION_KEY, {})
        else:
            self.store.set_state(self.POSITION_KEY, self.position.to_dict())
        self.store.set_state(
            self.ACCOUNT_KEY,
            {
                "realized_pnl_usdt": self.realized_pnl_usdt,
                "closed_positions": self.closed_positions,
                "starting_balance_usdt": (
                    self.settings.execution.paper_start_balance_usdt
                ),
                "equity_usdt": self.settings.execution.paper_start_balance_usdt
                + self.realized_pnl_usdt
                + (self.position.net_pnl_usdt if self.position else 0.0),
            },
        )

    @staticmethod
    def _next_timestamp(
        candidate_timestamp: int | None, interval_ms: int, now: int
    ) -> int:
        if candidate_timestamp and candidate_timestamp > now:
            return candidate_timestamp
        return now + interval_ms

    def open(
        self, candidate: Candidate, timestamp_ms: int | None = None
    ) -> PaperPosition:
        if self.position is not None:
            raise RuntimeError("paper position already open")
        timestamp = timestamp_ms or now_ms()
        long_interval = max(1, round(candidate.long_interval_hours * 3_600_000))
        short_interval = max(1, round(candidate.short_interval_hours * 3_600_000))
        round_trip_cost_bps = (
            candidate.fee_bps
            + candidate.slippage_bps
            + candidate.safety_bps
            + max(0.0, candidate.entry_basis_bps)
            + self.settings.execution.paper_entry_extra_bps
            + self.settings.execution.paper_exit_extra_bps
        )
        charged_costs = candidate.matched_notional_usdt * round_trip_cost_bps / 10_000.0
        self.position = PaperPosition(
            position_id=f"paper-{candidate.candidate_id}",
            candidate=candidate.to_dict(),
            opened_at_ms=timestamp,
            updated_at_ms=timestamp,
            base_amount=candidate.base_amount,
            long_next_funding_ms=self._next_timestamp(
                candidate.long_funding_timestamp_ms, long_interval, timestamp
            ),
            short_next_funding_ms=self._next_timestamp(
                candidate.short_funding_timestamp_ms, short_interval, timestamp
            ),
            long_interval_ms=long_interval,
            short_interval_ms=short_interval,
            funding_pnl_usdt=0.0,
            mark_pnl_usdt=0.0,
            charged_costs_usdt=charged_costs,
            funding_events=0,
        )
        self.store.append_event(
            "paper_opened",
            {
                "candidate": candidate.to_dict(),
                "charged_round_trip_costs_usdt": charged_costs,
            },
            self.position.position_id,
            timestamp,
        )
        self._persist()
        return self.position

    def _snapshot(
        self,
        snapshots: Mapping[tuple[str, str], MarketSnapshot],
        exchange: str,
        symbol: str,
    ) -> MarketSnapshot | None:
        return snapshots.get((exchange, symbol))

    def accrue(
        self,
        snapshots: Mapping[tuple[str, str], MarketSnapshot],
        timestamp_ms: int | None = None,
    ) -> float:
        if self.position is None:
            return 0.0
        timestamp = timestamp_ms or now_ms()
        candidate = self.position.candidate
        long = self._snapshot(
            snapshots, str(candidate["long_exchange"]), str(candidate["long_symbol"])
        )
        short = self._snapshot(
            snapshots, str(candidate["short_exchange"]), str(candidate["short_symbol"])
        )
        if long is not None and short is not None:
            long_entry = float(candidate["long_entry_price"])
            short_entry = float(candidate["short_entry_price"])
            long_pnl = self.position.base_amount * (long.quote.mark_price - long_entry)
            short_pnl = self.position.base_amount * (
                short_entry - short.quote.mark_price
            )
            self.position.mark_pnl_usdt = long_pnl + short_pnl
        delta = 0.0
        # Cap catch-up to avoid inventing months of funding from one stale quote.
        max_catch_up_events = 4
        if long is not None:
            count = 0
            while (
                timestamp >= self.position.long_next_funding_ms
                and count < max_catch_up_events
            ):
                notional = self.position.base_amount * long.quote.mark_price
                payment = -long.quote.funding_rate * notional
                delta += payment
                self.position.funding_pnl_usdt += payment
                self.position.funding_events += 1
                self.position.long_next_funding_ms += self.position.long_interval_ms
                count += 1
        if short is not None:
            count = 0
            while (
                timestamp >= self.position.short_next_funding_ms
                and count < max_catch_up_events
            ):
                notional = self.position.base_amount * short.quote.mark_price
                payment = short.quote.funding_rate * notional
                delta += payment
                self.position.funding_pnl_usdt += payment
                self.position.funding_events += 1
                self.position.short_next_funding_ms += self.position.short_interval_ms
                count += 1
        self.position.updated_at_ms = timestamp
        if delta:
            self.store.append_event(
                "paper_funding",
                {
                    "delta_usdt": delta,
                    "funding_pnl_usdt": self.position.funding_pnl_usdt,
                    "mark_pnl_usdt": self.position.mark_pnl_usdt,
                    "net_pnl_usdt": self.position.net_pnl_usdt,
                },
                self.position.position_id,
                timestamp,
            )
        self._persist()
        return delta

    def should_close(
        self,
        snapshots: Mapping[tuple[str, str], MarketSnapshot],
        timestamp_ms: int | None = None,
    ) -> tuple[bool, str]:
        if self.position is None:
            return False, "no_position"
        timestamp = timestamp_ms or now_ms()
        age_hours = (timestamp - self.position.opened_at_ms) / 3_600_000.0
        if age_hours >= self.settings.risk.max_hold_hours:
            return True, "max_hold_hours"
        candidate = self.position.candidate
        long = self._snapshot(
            snapshots, str(candidate["long_exchange"]), str(candidate["long_symbol"])
        )
        short = self._snapshot(
            snapshots, str(candidate["short_exchange"]), str(candidate["short_symbol"])
        )
        if long is None or short is None:
            return False, "market_data_incomplete"
        current_spread_bps_8h = (
            (short.quote.current_rate_per_hour - long.quote.current_rate_per_hour)
            * 8.0
            * 10_000.0
        )
        predicted_spread_bps_8h = (
            (short.quote.predicted_rate_per_hour - long.quote.predicted_rate_per_hour)
            * 8.0
            * 10_000.0
        )
        if current_spread_bps_8h <= self.settings.risk.exit_expected_net_bps:
            return True, "current_funding_spread_collapsed"
        if predicted_spread_bps_8h <= 0:
            return True, "predicted_funding_reversal"
        return False, "hold"

    def close(self, reason: str, timestamp_ms: int | None = None) -> float:
        if self.position is None:
            raise RuntimeError("no paper position")
        timestamp = timestamp_ms or now_ms()
        pnl = self.position.net_pnl_usdt
        position_id = self.position.position_id
        self.realized_pnl_usdt += pnl
        self.closed_positions += 1
        self.store.append_event(
            "paper_closed",
            {
                "reason": reason,
                "net_pnl_usdt": pnl,
                "funding_pnl_usdt": self.position.funding_pnl_usdt,
                "mark_pnl_usdt": self.position.mark_pnl_usdt,
                "charged_costs_usdt": self.position.charged_costs_usdt,
            },
            position_id,
            timestamp,
        )
        self.position = None
        self._persist()
        return pnl

    def summary(self) -> dict:
        unrealized = self.position.net_pnl_usdt if self.position else 0.0
        return {
            "mode": "paper",
            "starting_balance_usdt": self.settings.execution.paper_start_balance_usdt,
            "realized_pnl_usdt": self.realized_pnl_usdt,
            "unrealized_pnl_usdt": unrealized,
            "equity_usdt": self.settings.execution.paper_start_balance_usdt
            + self.realized_pnl_usdt
            + unrealized,
            "closed_positions": self.closed_positions,
            "open_position": self.position.to_dict() if self.position else None,
        }
