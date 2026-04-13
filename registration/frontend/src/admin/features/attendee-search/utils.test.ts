import { describe, expect, it } from "vitest";

import type { BadgeResult } from "@admin/api";

import { hasAnyExactMatch } from "./utils";

type AttendeeShape = BadgeResult["attendee"];

const makeBadge = (
  attendeeOverrides: Partial<AttendeeShape> = {},
  badgeOverrides: Partial<Omit<BadgeResult, "attendee">> = {},
): BadgeResult =>
  ({
    id: 1,
    badgeName: "Fluffy",
    abandoned: "",
    event: { id: 1, name: "Event" },
    printed: false,
    ...badgeOverrides,
    attendee: {
      firstName: "Jane",
      lastName: "Doe",
      preferredName: "",
      email: "jane@example.com",
      id: 1,
      ...attendeeOverrides,
    } as AttendeeShape,
  }) as BadgeResult;

describe("hasAnyExactMatch", () => {
  it("matches on the full legal name, case-insensitively", () => {
    expect(hasAnyExactMatch("jane doe", makeBadge())).toBe(true);
    expect(hasAnyExactMatch("JANE DOE", makeBadge())).toBe(true);
  });

  it("matches on the preferred name when present", () => {
    const badge = makeBadge({ preferredName: "Janey" });
    expect(hasAnyExactMatch("Janey Doe", badge)).toBe(true);
  });

  it("matches on the badge name", () => {
    expect(hasAnyExactMatch("Fluffy", makeBadge())).toBe(true);
  });

  it("does not match on unrelated input", () => {
    expect(hasAnyExactMatch("John Smith", makeBadge())).toBe(false);
    expect(hasAnyExactMatch("", makeBadge())).toBe(false);
  });

  it("does not consult preferredName when it is empty", () => {
    // " Doe" would falsely match a whitespace-prefixed preferred comparison
    // if the code didn't skip empty preferredName.
    expect(hasAnyExactMatch(" Doe", makeBadge())).toBe(false);
  });
});
