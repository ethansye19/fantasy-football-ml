export const APP_NAME = "Gridiron Projections";

export interface NavItem {
  href: string;
  label: string;
  /** Uses model outputs, so it is off until a model passes its test. */
  projection: boolean;
  description: string;
}

export const NAV_ITEMS: NavItem[] = [
  {
    href: "/projections",
    label: "Projections",
    projection: true,
    description: "Weekly ranges for every player: floor, median, ceiling and the chance of a big or a poor week.",
  },
  {
    href: "/start-sit",
    label: "Start/Sit",
    projection: true,
    description: "Compare two to four players and see how often each one outscores the others, with the reasons.",
  },
  {
    href: "/waivers",
    label: "Waivers",
    projection: true,
    description: "Free agents ranked by what they add to your lineup, each paired with a player you could drop.",
  },
  {
    href: "/trade",
    label: "Trade",
    projection: true,
    description: "Rest-of-season value over replacement, roster fit before and after, and a fairness band.",
  },
  {
    href: "/rankings",
    label: "Rankings",
    projection: true,
    description: "Rest-of-season rankings under your league's scoring, refreshed when a new run is published.",
  },
  {
    href: "/setup",
    label: "League",
    projection: false,
    description: "Import a Sleeper league or enter your scoring and roster rules by hand.",
  },
  {
    href: "/about",
    label: "About",
    projection: false,
    description: "How the model works, its limits, its evaluation status and the data sources.",
  },
];

export const ABOUT_ITEMS = [
  { href: "/about", label: "Overview" },
  { href: "/about/model-card", label: "Model card" },
  { href: "/about/limitations", label: "Limitations" },
  { href: "/about/attribution", label: "Data and attribution" },
] as const;

export function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}
