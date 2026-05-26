import { useEffect, useMemo, useRef } from 'react';
import { BlockNoteEditor, PartialBlock } from '@blocknote/core';
import { useCreateBlockNote } from '@blocknote/react';
import { BlockNoteView } from '@blocknote/mantine';
import '@blocknote/core/fonts/inter.css';
import '@blocknote/mantine/style.css';
import { uploadFile } from '../api/client';

interface Props {
  initialContent?: PartialBlock[];
  onChange: (json: PartialBlock[], html: string) => void;
}

export default function ContentEditor({ initialContent, onChange }: Props) {
  // BlockNote 에디터 인스턴스 — 한국어 라벨 + 업로드 핸들러
  const editor = useCreateBlockNote({
    initialContent: initialContent && initialContent.length > 0 ? initialContent : undefined,
    uploadFile: async (file: File) => {
      const url = await uploadFile(file);
      // 절대 URL이 아니면 same-origin으로 변환
      if (url.startsWith('/')) return window.location.origin + url;
      return url;
    },
    dictionary: koDictionary,
  });

  // 변경 시 부모에게 알림 (JSON + HTML)
  const lastJson = useRef<string>('');
  useEffect(() => {
    if (!editor) return;
    const handler = async () => {
      const json = editor.document;
      const jsonStr = JSON.stringify(json);
      if (jsonStr === lastJson.current) return;
      lastJson.current = jsonStr;
      try {
        const html = await editor.blocksToHTMLLossy(json);
        onChange(json as PartialBlock[], html);
      } catch (e) {
        console.error('blocksToHTMLLossy error:', e);
      }
    };
    editor.onChange(handler);
    handler();  // 초기 호출
  }, [editor, onChange]);

  return (
    <div className="bn-container">
      <BlockNoteView editor={editor} theme="light" />
    </div>
  );
}

// 간이 한국어 라벨 — 핵심 메뉴/플레이스홀더만 (BlockNote 기본 사전을 부분 override)
// BlockNote가 자체 사전을 갖고 있으나 영어. 풀 번역은 별도 패키지가 있지만 우선 핵심만.
const koDictionary: any = {
  slash_menu: {
    heading: { title: '큰 제목', subtext: '제목 1', aliases: ['h', 'heading1', '제목'], group: '제목' },
    heading_2: { title: '중간 제목', subtext: '제목 2', aliases: ['h2'], group: '제목' },
    heading_3: { title: '소제목', subtext: '제목 3', aliases: ['h3'], group: '제목' },
    numbered_list: { title: '번호 목록', subtext: '1. 2. 3.', aliases: ['ol', 'li'], group: '목록' },
    bullet_list: { title: '글머리 목록', subtext: '• • •', aliases: ['ul', 'list'], group: '목록' },
    check_list: { title: '체크 리스트', subtext: '☐ 항목', aliases: ['check'], group: '목록' },
    paragraph: { title: '본문', subtext: '일반 텍스트', aliases: ['p', 'text'], group: '기본' },
    table: { title: '표', subtext: '행/열 표', aliases: ['table'], group: '기본' },
    image: { title: '사진', subtext: '이미지 업로드', aliases: ['img', '사진'], group: '미디어' },
    video: { title: '동영상', subtext: '영상 임베드', aliases: ['vid'], group: '미디어' },
    audio: { title: '오디오', subtext: '음성 파일', aliases: ['audio'], group: '미디어' },
    file: { title: '파일', subtext: '파일 첨부', aliases: ['file'], group: '미디어' },
    code_block: { title: '코드 블록', subtext: '소스코드', aliases: ['code'], group: '기본' },
    emoji: { title: '이모지', subtext: '이모지 검색', aliases: ['emoji'], group: '기본' },
  },
  placeholders: {
    default: '내용을 입력하세요. 또는 "/" 입력해 블록 추가',
    heading: '제목',
    bulletListItem: '항목',
    numberedListItem: '항목',
    checkListItem: '체크 항목',
    new_comment: '댓글…',
    edit_comment: '편집…',
    comment_reply: '답글…',
  },
};
