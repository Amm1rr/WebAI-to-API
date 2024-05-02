import { render, screen } from "@testing-library/react";
import App from "./App";

test("renders endpoint link", () => {
  render(<App />);
  const linkElement = screen.getByText("v1/chat/completion");
  expect(linkElement).toBeInTheDocument();
});
