"""Config-driven fallback chains for the router (G21).

The main chain and the auxiliary chains are CONFIG, not code:
a caller supplies the provider lists (or takes the defaults
built from the router catalog) and this module turns them into
the ``ChainEntry`` lists ``puspa.router.fallback.call_with_fallback``
executes. Nothing here calls a provider.

Chains
------
- ``main``: the chain for the turn's own model calls. Default:
  the role's subscription -> cheap -> free chain from
  ``puspa.router.fallback.build_chain``.
- ``auxiliary``: one INDEPENDENT chain per auxiliary task kind —
  ``vision``, ``compression``, ``title``. An auxiliary failure
  never touches the main chain and never borrows its tiers: a
  title call that 429s falls through the title chain only.
  Defaults: ``vision`` uses the catalog's vision chain;
  ``compression`` and ``title`` use the reader chain (the
  cheapest models that can do short text work).

A chain is a list of ``ProviderRef`` (provider + model, with
optional explicit rates). Rates come from the router catalog
when the model is listed there; an unlisted model gets rate 0
and its cost is reported as unknown (``cost_rp=None`` on the
completion), never invented. At most three refs are used per
chain (the three fallback tiers); extras are reported, not
silently kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .complete import resolve_role
from .fallback import ChainEntry, build_chain
from .router import CATALOG

__all__ = [
    "AUXILIARY_KINDS",
    "DEFAULT_AUXILIARY_ROLES",
    "FallbackChainConfig",
    "ProviderRef",
    "chain_entries_for",
    "default_fallback_chain_config",
]

#: Auxiliary task kinds with their own independent chain.
AUXILIARY_KINDS = ("vision", "compression", "title")

#: Catalog role each auxiliary kind defaults to when no explicit
#: provider list is configured for it.
DEFAULT_AUXILIARY_ROLES = {
    "vision": "vision",
    "compression": "pembaca",
    "title": "pembaca",
}

#: Tier labels, in chain order (most expensive first).
_TIER_ORDER = ("subscription", "cheap", "free")


@dataclass(frozen=True)
class ProviderRef:
    """One provider entry in a configured chain.

    ``rp_per_1k_input`` / ``rp_per_1k_output`` are optional: when
    omitted they are read from the router catalog for a listed
    model, and 0 for an unlisted one (cost unknown, never
    invented).
    """

    provider: str
    model: str
    rp_per_1k_input: int | None = None
    rp_per_1k_output: int | None = None


def _catalog_row(provider: str, model: str) -> tuple | None:
    for row in CATALOG:
        if row[0] == provider and row[1] == model:
            return row
    return None


def _catalog_rates(provider: str, model: str) -> tuple[int, int]:
    row = _catalog_row(provider, model)
    return (row[3], row[4]) if row is not None else (0, 0)


def _refs_to_entries(refs: list[ProviderRef]) -> list[ChainEntry]:
    entries: list[ChainEntry] = []
    for tier, ref in zip(_TIER_ORDER, refs):
        rp_in = ref.rp_per_1k_input
        rp_out = ref.rp_per_1k_output
        if rp_in is None or rp_out is None:
            catalog_in, catalog_out = _catalog_rates(ref.provider, ref.model)
            rp_in = catalog_in if rp_in is None else rp_in
            rp_out = catalog_out if rp_out is None else rp_out
        row = _catalog_row(ref.provider, ref.model)
        entries.append(
            ChainEntry(
                tier=tier,
                provider=ref.provider,
                model=ref.model,
                family=row[2] if row is not None else "",
                rp_per_1k_input=rp_in,
                rp_per_1k_output=rp_out,
                vision=row[5] if row is not None else False,
            )
        )
    return entries


def _chain_to_refs(chain: list[ChainEntry]) -> list[ProviderRef]:
    return [
        ProviderRef(
            provider=entry.provider,
            model=entry.model,
            rp_per_1k_input=entry.rp_per_1k_input,
            rp_per_1k_output=entry.rp_per_1k_output,
        )
        for entry in chain
    ]


@dataclass
class FallbackChainConfig:
    """The configured fallback chains for one agent.

    ``main`` is the chain for the turn's own model calls;
    ``auxiliary`` maps each auxiliary kind to its own chain.
    An empty list means "no chain configured for that kind" —
    the caller then falls back to the catalog default for the
    kind (see :func:`chain_entries_for`), it never means "no
    fallback at all, fail the turn".
    """

    main: list[ProviderRef] = field(default_factory=list)
    auxiliary: dict[str, list[ProviderRef]] = field(default_factory=dict)

    def main_refs(self) -> list[ProviderRef]:
        return list(self.main)

    def auxiliary_refs(self, kind: str) -> list[ProviderRef]:
        if kind not in AUXILIARY_KINDS:
            raise ValueError(f"unknown auxiliary kind: {kind!r} (want one of {AUXILIARY_KINDS})")
        return list(self.auxiliary.get(kind, []))


def default_fallback_chain_config(role: str = "reader") -> FallbackChainConfig:
    """Build the default chains from the router catalog.

    The main chain is the role's 3-tier chain; each auxiliary
    kind gets its own chain from its default role. ``role`` is a
    catalog role name (``"pembaca"``, ...) or an English alias
    (``"reader"``, ...) — aliases are resolved, so a typo fails
    here instead of silently picking the wrong chain.
    """
    role = resolve_role(role)
    auxiliary = {
        kind: _chain_to_refs(build_chain(aux_role))
        for kind, aux_role in DEFAULT_AUXILIARY_ROLES.items()
    }
    return FallbackChainConfig(
        main=_chain_to_refs(build_chain(role)),
        auxiliary=auxiliary,
    )


def chain_entries_for(
    config: FallbackChainConfig | None,
    kind: str = "main",
    *,
    role: str = "reader",
) -> tuple[list[ChainEntry], list[str]]:
    """Resolve one chain to ``ChainEntry`` objects.

    ``kind`` is ``"main"`` or one of the auxiliary kinds. A
    configured list wins; an empty/missing list falls back to
    the catalog default for the kind (main: the role's chain;
    auxiliary: its default role's chain). Returns
    ``(entries, notes)``: ``notes`` reports every judgement
    call — a default used instead of config, refs beyond the
    three tiers dropped, an unlisted model with unknown rates.
    At most three entries are returned.
    """
    notes: list[str] = []
    refs: list[ProviderRef] = []
    if config is not None:
        refs = config.main_refs() if kind == "main" else config.auxiliary_refs(kind)
    if not refs:
        if kind == "main":
            refs = _chain_to_refs(build_chain(resolve_role(role)))
        else:
            refs = _chain_to_refs(build_chain(DEFAULT_AUXILIARY_ROLES[kind]))
        notes.append(f"{kind} chain: no providers configured; using the catalog default")
    if len(refs) > len(_TIER_ORDER):
        dropped = refs[len(_TIER_ORDER) :]
        notes.append(
            f"{kind} chain: {len(dropped)} provider(s) beyond the "
            f"{len(_TIER_ORDER)} tiers dropped: "
            + ", ".join(f"{ref.provider}/{ref.model}" for ref in dropped)
        )
        refs = refs[: len(_TIER_ORDER)]
    for ref in refs:
        if _catalog_rates(ref.provider, ref.model) == (0, 0) and (
            ref.rp_per_1k_input is None or ref.rp_per_1k_output is None
        ):
            notes.append(
                f"{kind} chain: {ref.provider}/{ref.model} is not in the "
                "router catalog; its cost is unknown (rate 0)"
            )
    return _refs_to_entries(refs), notes
