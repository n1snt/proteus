import { ApiError } from "../api";

export function message(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}

export function staleMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    return "This plan is stale because the branch changed. Refresh the branch and create a new preview.";
  }
  return message(error);
}

export function short(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : "pending";
}

export function displayTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function formatValue(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value) || "none";
}
