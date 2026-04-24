import { faUser } from "@fortawesome/free-solid-svg-icons";
import { render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";

import { IconAndLabel } from "./icon-and-label";

describe("IconAndLabel", () => {
  it("renders the label text next to the icon", () => {
    const { container } = render(() => (
      <IconAndLabel icon={faUser}>My Profile</IconAndLabel>
    ));
    expect(screen.getByText(/My Profile/)).toBeInTheDocument();
    // solid-fa renders an <svg>; make sure one is present
    expect(container.querySelector("svg")).toBeInTheDocument();
  });

  it("accepts a JSX element as the label", () => {
    render(() => (
      <IconAndLabel icon={faUser}>
        <span data-testid="nested">Nested</span>
      </IconAndLabel>
    ));
    expect(screen.getByTestId("nested")).toHaveTextContent("Nested");
  });
});
