import ReactDOM from 'react-dom/client';
import App from './App';
import ErrorBoundary from './components/ErrorBoundary';
import './styles.css';

// StrictMode 제거 (BlockNote가 double-mount에 민감)
ReactDOM.createRoot(document.getElementById('root')!).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>
);

// 전역 미처리 에러도 콘솔에
window.addEventListener('error', (e) => {
  console.error('[window.error]', e.error || e.message);
});
window.addEventListener('unhandledrejection', (e) => {
  console.error('[unhandled]', e.reason);
});
