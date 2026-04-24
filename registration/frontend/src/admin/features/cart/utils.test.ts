import { describe, expect, it } from "vitest";

import { cleanMoneyAmount, createAutoPrintCheck } from "./utils";
import type { BadgeCart } from "@admin/api";

// ---------------------------------------------------------------------------
// cleanMoneyAmount
// ---------------------------------------------------------------------------

describe("cleanMoneyAmount", () => {
  it("returns $0.00 for null/empty/? inputs", () => {
    expect(cleanMoneyAmount(undefined)).toBe("$0.00");
    expect(cleanMoneyAmount("")).toBe("$0.00");
    expect(cleanMoneyAmount("?")).toBe("$0.00");
  });

  it("passes percentages through unchanged", () => {
    expect(cleanMoneyAmount("15%")).toBe("15%");
  });

  it("formats bare numbers as USD", () => {
    expect(cleanMoneyAmount("10")).toBe("$10");
    expect(cleanMoneyAmount("10.5")).toBe("$10.50");
  });

  it("strips a leading dollar sign before parsing", () => {
    expect(cleanMoneyAmount("$12.34")).toBe("$12.34");
  });

  it("supports negative amounts", () => {
    expect(cleanMoneyAmount("-5")).toBe("-$5");
  });

  it("returns the original string unchanged when parsing fails", () => {
    expect(cleanMoneyAmount("not a number")).toBe("not a number");
  });
});

// ---------------------------------------------------------------------------
// createAutoPrintCheck
// ---------------------------------------------------------------------------

const makeBadge = (overrides: Partial<BadgeCart> = {}): BadgeCart =>
  ({
    id: 1,
    abandoned: "Unpaid",
    ...overrides,
  }) as BadgeCart;

describe("createAutoPrintCheck", () => {
  it("returns false on the first call (no previous state)", () => {
    const check = createAutoPrintCheck();
    expect(check([1], [makeBadge({ id: 1, abandoned: "Paid" })])).toBe(false);
  });

  it("returns false when the badge list is empty", () => {
    const check = createAutoPrintCheck();
    check([1], [makeBadge({ id: 1, abandoned: "Unpaid" })]);
    expect(check([1], [])).toBe(false);
  });

  it("returns false when ids are not in the printable set", () => {
    const check = createAutoPrintCheck();
    check([], [makeBadge({ id: 1, abandoned: "Unpaid" })]);
    expect(
      check([], [makeBadge({ id: 1, abandoned: "Paid" })]),
    ).toBe(false);
  });

  it("returns true when a badge transitions to Paid and is printable", () => {
    const check = createAutoPrintCheck();
    check([1], [makeBadge({ id: 1, abandoned: "Unpaid" })]);
    expect(
      check([1], [makeBadge({ id: 1, abandoned: "Paid" })]),
    ).toBe(true);
  });

  it("returns false when the badge list length changes", () => {
    const check = createAutoPrintCheck();
    check([1], [makeBadge({ id: 1, abandoned: "Unpaid" })]);
    expect(
      check([1, 2], [
        makeBadge({ id: 1, abandoned: "Paid" }),
        makeBadge({ id: 2, abandoned: "Paid" }),
      ]),
    ).toBe(false);
  });
});
