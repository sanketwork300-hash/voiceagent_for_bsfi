/** Normalised API error. `userMessage` is safe to show to customers; `detail` is for operators. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public userMessage: string,
    public reference: string | null,
    public detail?: string,
  ) {
    super(userMessage);
    this.name = "ApiError";
  }

  get isUnavailable() {
    return this.status === 0 || this.status >= 500;
  }
}

export function messageForStatus(status: number): string {
  if (status === 0) return "We couldn't reach the service. Check your connection and try again.";
  if (status === 401) return "Your session has ended. Sign in again to continue.";
  if (status === 403) return "You don't have permission to do this.";
  if (status === 404) return "We couldn't find what you were looking for.";
  if (status === 409) return "This changed since you loaded it. Refresh and try again.";
  if (status === 413) return "That file is too large.";
  if (status === 415) return "That file type isn't supported.";
  if (status === 422 || status === 400) return "Some of the information provided isn't valid.";
  if (status === 429) return "Too many requests. Wait a moment and try again.";
  return "The service is temporarily unavailable. Nothing was changed.";
}
