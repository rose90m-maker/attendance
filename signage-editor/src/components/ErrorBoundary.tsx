import { Component, ReactNode } from 'react';

interface State {
  error: Error | null;
  info: string;
}

export default class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null, info: '' };

  static getDerivedStateFromError(error: Error): State {
    return { error, info: '' };
  }

  componentDidCatch(error: Error, info: any) {
    console.error('[ErrorBoundary]', error, info);
    this.setState({ error, info: info?.componentStack || String(info) });
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 40, maxWidth: 900, margin: '40px auto', background: '#fff', borderRadius: 8, boxShadow: '0 2px 10px rgba(0,0,0,0.1)' }}>
          <h2 style={{ color: '#dc2626', marginTop: 0 }}>⚠️ 에디터 오류</h2>
          <p style={{ color: '#374151' }}>화면 렌더링 중 오류가 발생했습니다. 아래 정보를 확인하세요.</p>
          <pre style={{ background: '#fef2f2', color: '#7f1d1d', padding: 14, borderRadius: 6, overflow: 'auto', fontSize: '0.85rem' }}>
            {this.state.error.name}: {this.state.error.message}
            {'\n\n'}
            {this.state.error.stack}
          </pre>
          {this.state.info && (
            <details style={{ marginTop: 14 }}>
              <summary style={{ cursor: 'pointer', color: '#6b7280' }}>컴포넌트 스택</summary>
              <pre style={{ background: '#f3f4f6', padding: 12, fontSize: '0.78rem', overflow: 'auto' }}>{this.state.info}</pre>
            </details>
          )}
          <div style={{ marginTop: 20, display: 'flex', gap: 8 }}>
            <button onClick={() => window.location.reload()} style={{ padding: '8px 16px', background: '#1e40af', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer' }}>
              새로고침
            </button>
            <a href="/signage/contents" style={{ padding: '8px 16px', background: '#f3f4f6', color: '#374151', textDecoration: 'none', borderRadius: 6 }}>
              콘텐츠 목록으로
            </a>
            <a href="/signage/editor/new?legacy=1" style={{ padding: '8px 16px', background: '#fef3c7', color: '#92400e', textDecoration: 'none', borderRadius: 6 }}>
              레거시 에디터로 우회
            </a>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
