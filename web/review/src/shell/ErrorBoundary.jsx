import { Component } from "react";

/** Keeps a crash inside one tool from blanking the whole shell. */
export class ErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("tool crashed", error, info.componentStack);
  }

  render() {
    if (this.state.error === null) return this.props.children;
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center text-sm text-rose-700">
        <p>הכלי נתקל בשגיאה ולא יכול להמשיך.</p>
        <p dir="auto" className="font-mono text-xs">
          {String(this.state.error?.message ?? this.state.error)}
        </p>
        <button type="button" className="btn btn-sm" onClick={() => this.setState({ error: null })}>
          נסו שוב
        </button>
      </div>
    );
  }
}
