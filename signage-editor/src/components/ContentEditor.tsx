import { useEffect, useRef } from 'react';
import { useCreateBlockNote } from '@blocknote/react';
import { BlockNoteView } from '@blocknote/mantine';
import '@blocknote/core/fonts/inter.css';
import '@blocknote/mantine/style.css';
import { uploadFile } from '../api/client';

interface Props {
  initialContent?: any[];
  onChange: (json: any[], html: string) => void;
}

export default function ContentEditor({ initialContent, onChange }: Props) {
  // BlockNote 에디터 — 기본 영문 사전 사용 (한국어 사전은 추후 별도 작업)
  const editor = useCreateBlockNote({
    initialContent: initialContent && initialContent.length > 0 ? initialContent : undefined,
    uploadFile: async (file: File) => {
      const url = await uploadFile(file);
      if (url.startsWith('/')) return window.location.origin + url;
      return url;
    },
  });

  // 변경 시 부모에게 알림 (JSON + HTML)
  const lastJson = useRef<string>('');
  useEffect(() => {
    if (!editor) return;
    const handler = async () => {
      try {
        const json = editor.document;
        const jsonStr = JSON.stringify(json);
        if (jsonStr === lastJson.current) return;
        lastJson.current = jsonStr;
        const html = await editor.blocksToHTMLLossy(json);
        onChange(json, html);
      } catch (e) {
        console.error('editor onChange error:', e);
      }
    };
    editor.onChange(handler);
    // 초기 1회 호출
    handler();
  }, [editor, onChange]);

  return (
    <div className="bn-container">
      <BlockNoteView editor={editor} theme="light" />
    </div>
  );
}
