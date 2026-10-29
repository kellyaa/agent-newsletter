const MONTH_NAMES = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

export interface MonthEntry {
  key: string;
  label: string;
  count: number;
  href: string;
}

export interface MonthBucket {
  key: string;
  issues: any[];
}

function monthKey(date: string): string {
  return date.slice(0, 7);
}

function monthLabel(key: string): string {
  const [y, m] = key.split("-").map(Number);
  return `${MONTH_NAMES[m - 1]} ${y}`;
}

export function groupByMonth(issues: any[]): MonthBucket[] {
  const sorted = [...issues].sort((a, b) => (a.data.date < b.data.date ? 1 : -1));
  const buckets = new Map<string, any[]>();
  for (const issue of sorted) {
    const k = monthKey(issue.data.date);
    const arr = buckets.get(k);
    if (arr) arr.push(issue);
    else buckets.set(k, [issue]);
  }
  // `sorted` is newest-first and a month key is a prefix of the date, so the
  // Map's insertion order is already newest-month-first; no second sort needed.
  return [...buckets.entries()].map(([key, issues]) => ({ key, issues }));
}

/**
 * `editionSlug` prefixes every href with `/<slug>`: the latest month links to
 * the edition front page (`/<slug>/`), older months to `/<slug>/months/<key>/`.
 */
export function buildMonthList(buckets: MonthBucket[], editionSlug?: string): MonthEntry[] {
  if (buckets.length === 0) return [];
  const latestKey = buckets[0].key;
  const prefix = editionSlug ? `/${editionSlug}` : "";
  return buckets.map((b) => ({
    key: b.key,
    label: monthLabel(b.key),
    count: b.issues.length,
    href: b.key === latestKey ? `${prefix}/` : `${prefix}/months/${b.key}/`,
  }));
}

export interface ArchiveDay {
  date: string;
  /** e.g. "Mon 15" */
  label: string;
  href: string;
  current: boolean;
}

export interface ArchiveMonth {
  key: string;
  label: string;
  /** True for the month containing the current issue. */
  open: boolean;
  days: ArchiveDay[];
}

const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function dayLabel(date: string): string {
  const [y, m, d] = date.split("-").map(Number);
  const dow = DAY_NAMES[new Date(Date.UTC(y, m - 1, d, 12)).getUTCDay()];
  return `${dow} ${d}`;
}

/**
 * Archive tree for the issue-page sidebar: months newest first, each with its
 * days newest first. Hrefs are `/<slug>/issues/<date>/` (no BASE_URL; callers
 * prefix it). `currentDate` marks the current day and opens its month.
 */
export function buildArchive(buckets: MonthBucket[], editionSlug: string, currentDate: string): ArchiveMonth[] {
  const currentKey = monthKey(currentDate);
  return buckets.map((b) => ({
    key: b.key,
    label: monthLabel(b.key),
    open: b.key === currentKey,
    days: b.issues.map((i) => ({
      date: i.data.date,
      label: dayLabel(i.data.date),
      href: `/${editionSlug}/issues/${i.data.date}/`,
      current: i.data.date === currentDate,
    })),
  }));
}
