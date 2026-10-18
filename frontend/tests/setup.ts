import "@testing-library/jest-dom/vitest";

// jsdom does not implement layout APIs that real browsers have.
Element.prototype.scrollIntoView = function scrollIntoView() {};
