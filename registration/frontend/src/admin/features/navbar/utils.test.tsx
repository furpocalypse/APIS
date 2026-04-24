import { Big } from "big.js";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { KNOWN_SHORTCUTS, amountRequest, mutateThenToast } from "./utils";

// Kobalte's toaster uses a singleton that assumes a Toaster.Region is
// mounted. We only care that our helpers call the mutation with the right
// args — stub the toaster entirely.
vi.mock("@kobalte/core/toast", () => ({
  toaster: { show: vi.fn() },
}));

import { toaster } from "@kobalte/core/toast";

describe("KNOWN_SHORTCUTS", () => {
  it("has a non-empty, unique set of shortcut chords", () => {
    expect(KNOWN_SHORTCUTS.length).toBeGreaterThan(0);
    const chords = KNOWN_SHORTCUTS.map((s) => s.shortcut);
    expect(new Set(chords).size).toBe(chords.length);
  });

  it("gives every shortcut a non-empty description", () => {
    for (const s of KNOWN_SHORTCUTS) {
      expect(s.description.length).toBeGreaterThan(0);
    }
  });
});

describe("mutateThenToast", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("invokes mutate() and shows a success toast on success", () => {
    const onSuccessFromArg = vi.fn();
    const mutation = {
      mutate: vi.fn((_args, cbs) => cbs.onSuccess()),
    } as unknown as Parameters<typeof mutateThenToast>[0];
    mutateThenToast(mutation, 42, "Nice", onSuccessFromArg);
    expect(mutation.mutate).toHaveBeenCalledOnce();
    expect(onSuccessFromArg).toHaveBeenCalledOnce();
    expect(toaster.show).toHaveBeenCalledOnce();
  });

  it("shows a danger toast on error", () => {
    const mutation = {
      mutate: vi.fn((_args, cbs) => cbs.onError(new Error("nope"))),
    } as unknown as Parameters<typeof mutateThenToast>[0];
    mutateThenToast(mutation, 1, "ok");
    expect(toaster.show).toHaveBeenCalledOnce();
  });
});

describe("amountRequest", () => {
  const makeMutation = () => ({ mutate: vi.fn() }) as unknown as Parameters<
    typeof amountRequest
  >[0];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("does nothing when the user cancels the prompt", () => {
    vi.spyOn(window, "prompt").mockReturnValue(null);
    const mutation = makeMutation();
    amountRequest(mutation, "Enter amount");
    expect(mutation.mutate).not.toHaveBeenCalled();
  });

  it("alerts on invalid numeric input and does not mutate", () => {
    vi.spyOn(window, "prompt").mockReturnValue("not a number");
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    const mutation = makeMutation();
    amountRequest(mutation, "Enter amount");
    expect(alertSpy).toHaveBeenCalled();
    expect(mutation.mutate).not.toHaveBeenCalled();
  });

  it("mutates with a Big instance on valid input", () => {
    vi.spyOn(window, "prompt").mockReturnValue("12.50");
    const mutation = makeMutation();
    amountRequest(mutation, "Enter amount");
    expect(mutation.mutate).toHaveBeenCalledOnce();
    const [arg] = (mutation.mutate as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(arg).toBeInstanceOf(Big);
    expect((arg as Big).toString()).toBe("12.5");
  });
});
