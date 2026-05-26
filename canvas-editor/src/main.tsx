import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './styles/globals.css';

class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error, info: any) { console.error('[Canvas Editor]', error, info); }
  render() {
    if (this.state.error) {
      return (
        <div className="p-10 max-w-3xl mx-auto bg-white rounded-lg shadow mt-12">
          <h2 className="text-red-600 text-xl font-bold mb-3">⚠️ 에디터 오류</h2>
          <pre className="bg-red-50 p-3 rounded text-xs text-red-900 overflow-auto">{this.state.error.message}{'\n\n'}{this.state.error.stack}</pre>
          <div className="mt-4 flex gap-2">
            <button onClick={() => location.reload()} className="px-4 py-2 bg-blue-600 text-white rounded">새로고침</button>
            <a href="/signage/contents" className="px-4 py-2 bg-gray-200 rounded">목록으로</a>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <ErrorBoundary><App /></ErrorBoundary>
);
