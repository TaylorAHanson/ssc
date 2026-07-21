import { Component, type ErrorInfo, type ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';

interface Props {
  children: ReactNode;
  /** Optional custom fallback. Receives the caught error + a reset fn. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
  /**
   * When any value in this array changes, the boundary auto-resets. Pass inputs
   * that "fix" the render (e.g. the open workflow id) so a one-off bad render
   * doesn't wedge the panel after the user navigates.
   */
  resetKeys?: unknown[];
  /** Short label for the default fallback, e.g. "the workflow editor". */
  label?: string;
}

interface State {
  error: Error | null;
}

function resetKeysChanged(prev?: unknown[], next?: unknown[]): boolean {
  if (prev === next) return false;
  if (!prev || !next || prev.length !== next.length) return true;
  return prev.some((v, i) => !Object.is(v, next[i]));
}

/**
 * A lightweight React error boundary. Catches render/lifecycle errors in its
 * subtree and shows a small recoverable panel instead of letting the error
 * unmount the whole app (a blank white screen). Use it to wrap risky, data-driven
 * regions like the workflow editor/graph preview.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Log for debugging without taking down the app.
    console.error('ErrorBoundary caught an error:', error, info.componentStack);
  }

  componentDidUpdate(prev: Props): void {
    if (this.state.error && resetKeysChanged(prev.resetKeys, this.props.resetKeys)) {
      this.reset();
    }
  }

  reset = (): void => this.setState({ error: null });

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);

    const { label } = this.props;
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-8 text-center border border-red-100 bg-red-50/40 rounded-lg">
        <AlertTriangle className="w-8 h-8 text-red-500" />
        <div className="text-sm font-semibold text-gray-900">
          Something went wrong{label ? ` in ${label}` : ''}.
        </div>
        <div className="text-xs text-gray-500 max-w-md break-words font-mono">{error.message}</div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={this.reset}
            className="text-xs px-3 py-1.5 rounded-md border border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
          >
            Try again
          </button>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="text-xs px-3 py-1.5 rounded-md bg-gray-900 text-white hover:bg-gray-800"
          >
            Reload page
          </button>
        </div>
      </div>
    );
  }
}

export default ErrorBoundary;
