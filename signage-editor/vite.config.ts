import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 빌드 산출물은 Flask static 디렉토리로 직접 출력 (배포 단순화)
export default defineConfig({
  plugins: [react()],
  // Flask가 /static/signage-editor/ 경로로 서빙
  base: '/static/signage-editor/',
  build: {
    outDir: '../static/signage-editor',
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      output: {
        // 캐시 buster 친화적 해시
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      }
    }
  },
  server: {
    port: 5173,
    proxy: {
      // 개발 시 Flask 백엔드로 프록시
      '/signage/api': 'http://localhost:5050',
      '/static': 'http://localhost:5050',
    }
  }
});
