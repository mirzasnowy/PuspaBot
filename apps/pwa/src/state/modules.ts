/**
 * Modules screen data: the two built-in modules from revision 7
 * §2.12, plus the custom-module flow (§2.13). Modules open as
 * panels inside this screen — they never add an 8th screen.
 * There is no module registry API yet, so enable/pause state is
 * not invented here: built-ins are always available, and custom
 * modules are created through chat.
 */
export interface BuiltinModule {
  id: string;
  name: string;
  description: string;
  status: "available";
}

export const BUILTIN_MODULES: BuiltinModule[] = [
  {
    id: "price_compare",
    name: "Price Compare",
    description:
      "Compare new-product prices across Tokopedia, Shopee, and Blibli. Every price carries evidence: source, fetch time, and a checker verdict.",
    status: "available",
  },
  {
    id: "bill_autopilot",
    name: "Bill Autopilot",
    description:
      "Pay PLN, PDAM, mobile credit, and data on schedule. Every payment waits in the approval queue first — nothing leaves without one tap.",
    status: "available",
  },
];

/** hermes-agent Skills page, as a section: skills ship inside modules. */
export const SKILLS_NOTE =
  "Skills ship inside modules and flows. A skill registry with versions " +
  "and rollback is a follow-up (G23); modules created in chat appear here once the registry lands.";
