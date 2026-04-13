import { render, screen } from "@solidjs/testing-library";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ErrorCard } from "./error-card";

describe("ErrorCard", () => {
  it("renders the title and stringified error", () => {
    render(() => (
      <ErrorCard title="Failure" err={new Error("boom")} reset={() => {}} />
    ));
    expect(screen.getByText("Failure")).toBeInTheDocument();
    expect(screen.getByText(/boom/)).toBeInTheDocument();
  });

  it("fires reset() when the Reset button is clicked", async () => {
    const reset = vi.fn();
    render(() => (
      <ErrorCard title="Failure" err={new Error("boom")} reset={reset} />
    ));
    await userEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it("accepts non-Error values and coerces them via toString", () => {
    render(() => (
      <ErrorCard title="Problem" err="plain string" reset={() => {}} />
    ));
    expect(screen.getByText("plain string")).toBeInTheDocument();
  });
});
