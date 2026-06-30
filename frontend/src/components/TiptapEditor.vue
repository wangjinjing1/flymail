<template>
  <div class="tiptap-editor">
    <!-- 工具栏 -->
    <div class="editor-toolbar" v-if="editor">
      <template v-for="btn in toolbarButtons" :key="btn.name">
        <!-- 下拉菜单按钮：字号/字体/颜色 -->
        <div v-if="btn.isDropdown" class="toolbar-dropdown">
          <button class="toolbar-btn" :title="btn.title" type="button">
            <span v-if="btn.dropdownType === 'fontFamily'">{{ currentFontLabel }} <small>▼</small></span>
            <span v-else-if="btn.dropdownType === 'fontSize'">{{ currentSizeLabel }} <small>▼</small></span>
            <span v-else-if="btn.dropdownType === 'lineHeight'" class="dropdown-icon-label"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/><path d="M1 6h1M1 12h1M1 18h1" stroke-width="3"/></svg><small>▼</small></span>
            <span v-else-if="btn.dropdownType === 'table'" class="dropdown-icon-label"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/></svg><small>▼</small></span>
            <span v-else-if="btn.dropdownType === 'color'" class="color-btn-inner">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 20h16"/><path d="M9.354 4H14.646L18 16H6L9.354 4z"/></svg>
              <span class="color-indicator" :style="{ background: currentColor }"></span>
            </span>
          </button>
          <div class="dropdown-menu">
            <!-- 字号下拉 -->
            <template v-if="btn.dropdownType === 'fontSize'">
              <button v-for="size in fontSizes" :key="size" class="dropdown-item" @click="applyFontSize(size)">
                <span :style="{ fontSize: size }">{{ size }}</span>
              </button>
              <button class="dropdown-item" @click="applyFontSize(null)">默认</button>
            </template>
            <!-- 字体下拉 -->
            <template v-if="btn.dropdownType === 'fontFamily'">
              <button v-for="f in fontFamilies" :key="f.value" class="dropdown-item" :style="{ fontFamily: f.value || 'inherit' }" @click="applyFontFamily(f.value)">
                {{ f.label }}
              </button>
            </template>
            <!-- 行间距下拉 -->
            <template v-if="btn.dropdownType === 'lineHeight'">
              <button v-for="h in lineHeightOptions" :key="h.value" class="dropdown-item" @click="applyLineHeight(h.value)">
                {{ h.label }}
              </button>
              <button class="dropdown-item" @click="applyLineHeight(null)">默认</button>
            </template>
            <!-- 颜色下拉 -->
            <template v-if="btn.dropdownType === 'color'">
              <div class="color-grid">
                <button v-for="c in presetColors" :key="c" class="color-swatch" :style="{ background: c }" @click="applyColor(c)"></button>
              </div>
              <button class="dropdown-item" @click="applyColor(null)">默认颜色</button>
            </template>
            <!-- 表格操作下拉 -->
            <template v-if="btn.dropdownType === 'table'">
              <button class="dropdown-item" @click="editor?.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()">插入表格</button>
              <div class="dropdown-divider"></div>
              <button class="dropdown-item" :disabled="!editor?.isActive('table')" @click="editor?.chain().focus().addRowBefore().run()">上方插入行</button>
              <button class="dropdown-item" :disabled="!editor?.isActive('table')" @click="editor?.chain().focus().addRowAfter().run()">下方插入行</button>
              <button class="dropdown-item" :disabled="!editor?.isActive('table')" @click="editor?.chain().focus().addColumnBefore().run()">左侧插入列</button>
              <button class="dropdown-item" :disabled="!editor?.isActive('table')" @click="editor?.chain().focus().addColumnAfter().run()">右侧插入列</button>
              <div class="dropdown-divider"></div>
              <button class="dropdown-item" :disabled="!editor?.isActive('table')" @click="editor?.chain().focus().mergeCells().run()">合并单元格</button>
              <button class="dropdown-item" :disabled="!editor?.isActive('table')" @click="editor?.chain().focus().splitCell().run()">拆分单元格</button>
              <div class="dropdown-divider"></div>
              <button class="dropdown-item danger" :disabled="!editor?.isActive('table')" @click="editor?.chain().focus().deleteRow().run()">删除行</button>
              <button class="dropdown-item danger" :disabled="!editor?.isActive('table')" @click="editor?.chain().focus().deleteColumn().run()">删除列</button>
              <button class="dropdown-item danger" :disabled="!editor?.isActive('table')" @click="editor?.chain().focus().deleteTable().run()">删除表格</button>
            </template>
          </div>
        </div>
        <!-- 普通按钮 -->
        <button
          v-else
          class="toolbar-btn"
          :class="{ active: btn.isActive?.() }"
          @click="btn.action"
          :title="btn.title"
          type="button"
        >
          <span v-html="btn.icon"></span>
        </button>
      </template>

      <!-- Emoji 选择器 -->
      <div class="toolbar-dropdown emoji-dropdown">
        <button class="toolbar-btn" title="表情" type="button" @click="showEmojiPicker = !showEmojiPicker">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>
        </button>
        <div v-if="showEmojiPicker" class="emoji-picker">
          <!-- 分类标签 -->
          <div class="emoji-tabs">
            <button
              v-for="(cat, i) in emojiCategories"
              :key="cat.name"
              class="emoji-tab"
              :class="{ active: activeEmojiCategory === i }"
              @click="activeEmojiCategory = i"
              type="button"
            >{{ cat.name }}</button>
          </div>
          <!-- Emoji 网格 -->
          <div class="emoji-grid">
            <button
              v-for="emoji in emojiCategories[activeEmojiCategory].emojis"
              :key="emoji"
              class="emoji-btn"
              @click="insertEmoji(emoji)"
              type="button"
            >{{ emoji }}</button>
          </div>
        </div>
      </div>
    </div>
    <!-- 编辑区域 -->
    <editor-content :editor="editor" class="editor-content" />
  </div>
</template>

<script setup lang="ts">
import { useEditor, EditorContent } from '@tiptap/vue-3';
import StarterKit from '@tiptap/starter-kit';
import Link from '@tiptap/extension-link';
import Image from '@tiptap/extension-image';
import Placeholder from '@tiptap/extension-placeholder';
import Underline from '@tiptap/extension-underline';
import { TextStyle } from '@tiptap/extension-text-style';
import Color from '@tiptap/extension-color';
import FontFamily from '@tiptap/extension-font-family';
import { Table } from '@tiptap/extension-table';
import { TableRow } from '@tiptap/extension-table-row';
import { TableCell } from '@tiptap/extension-table-cell';
import { TableHeader } from '@tiptap/extension-table-header';
import Paragraph from '@tiptap/extension-paragraph';
import { watch, onBeforeUnmount, computed, ref, onMounted } from 'vue';

const props = defineProps<{
  modelValue?: string;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: string];
}>();

// 自定义 TextStyle：扩展 fontSize 和 lineHeight 属性
// 注意：只能扩展一次 TextStyle，否则多个扩展操作同一个 mark 会冲突
const CustomTextStyle = TextStyle.extend({
  name: 'textStyle',
  addAttributes() {
    return {
      ...this.parent?.(),
      // 字号属性
      fontSize: {
        default: null,
        parseHTML: (element: HTMLElement) => element.style.fontSize?.replace(/['"]+/g, '') || null,
        renderHTML: (attributes: Record<string, any>) => {
          if (!attributes.fontSize) return {};
          return { style: `font-size: ${attributes.fontSize}` };
        },
      },
      // 行间距属性
      lineHeight: {
        default: null,
        parseHTML: (element: HTMLElement) => element.style.lineHeight?.replace(/['"]+/g, '') || null,
        renderHTML: (attributes: Record<string, any>) => {
          if (!attributes.lineHeight) return {};
          return { style: `line-height: ${attributes.lineHeight}` };
        },
      },
    };
  },
  addCommands() {
    return {
      // 设置字号
      setFontSize: (size: string) => ({ chain }: { chain: () => any }) => chain().setMark('textStyle', { fontSize: size }).run(),
      // 取消字号
      unsetFontSize: () => ({ chain }: { chain: () => any }) => chain().setMark('textStyle', { fontSize: null }).removeEmptyTextStyle().run(),
      // 设置行间距
      setLineHeight: (height: string) => ({ chain }: { chain: () => any }) => chain().setMark('textStyle', { lineHeight: height }).run(),
      // 取消行间距
      unsetLineHeight: () => ({ chain }: { chain: () => any }) => chain().setMark('textStyle', { lineHeight: null }).removeEmptyTextStyle().run(),
    } as any;
  },
});

// 自定义段落扩展：支持 indent 属性实现段落缩进
const CustomParagraph = Paragraph.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      indent: {
        default: 0,
        parseHTML: (element: HTMLElement) => {
          const ml = element.style.marginLeft;
          if (ml && ml.endsWith('px')) {
            return Math.round(parseInt(ml) / 40);
          }
          return 0;
        },
        renderHTML: (attributes: Record<string, any>) => {
          if (!attributes.indent || attributes.indent === 0) return {};
          return { style: `margin-left: ${attributes.indent * 40}px` };
        },
      },
    };
  },
});

// 保存编辑器失焦时的 storedMarks，用于下拉菜单操作后恢复样式状态
let savedStoredMarks: any[] | null = null;

const editor = useEditor({
  extensions: [
    StarterKit.configure({
      heading: { levels: [1, 2, 3] },
      paragraph: false,
    }),
    CustomParagraph,
    Underline,
    CustomTextStyle,
    Color,
    FontFamily,
    Table.configure({ resizable: true }),
    TableRow,
    TableCell,
    TableHeader,
    Link.configure({
      openOnClick: false,
      HTMLAttributes: { target: '_blank', rel: 'noopener noreferrer' },
    }),
    Image,
    Placeholder.configure({
      placeholder: '写点什么...',
    }),
  ],
  content: props.modelValue || '',
  onUpdate: ({ editor }) => {
    emit('update:modelValue', editor.getHTML());
  },
  onBlur: ({ editor }) => {
    // 失焦时保存 storedMarks，防止点击下拉菜单时丢失样式状态
    savedStoredMarks = (editor as any).storedMarks ? [...(editor as any).storedMarks] : null;
  },
});

/**
 * 恢复编辑器失焦前的 storedMarks
 * 解决：点击下拉菜单时编辑器失焦，storedMarks 被清除，
 * 导致后续输入不会应用已选中的字体/字号/颜色等样式
 */
const restoreStoredMarks = (e: any) => {
  if (savedStoredMarks && e.state.selection.empty) {
    savedStoredMarks.forEach((mark: any) => {
      e.commands.setStoredMark(mark);
    });
  }
};

// 外部 modelValue 变化时同步到编辑器
watch(() => props.modelValue, (val) => {
  if (!editor.value) return;
  const html = editor.value.getHTML();
  if (val !== undefined && val !== html) {
    editor.value.commands.setContent(val, { emitUpdate: false });
  }
});

onBeforeUnmount(() => {
  editor.value?.destroy();
});

/** 暴露给父组件：插入文本（如 emoji） */
function insertText(text: string) {
  if (!editor.value) return;
  editor.value.chain().focus().insertContent(text).run();
}

defineExpose({ insertText, getHTML: () => editor.value?.getHTML() || '' });

// ---- Emoji 选择器 ----
const showEmojiPicker = ref(false);
const activeEmojiCategory = ref(0);

/** Emoji 分类数据 */
const emojiCategories = [
  { name: '常用', emojis: ['😀','😂','🤣','😊','😍','🥰','😘','😜','🤗','🤔','😎','🥳','😢','😡','🤯','😴','🤮','👍','👎','👏','🙏','💪','❤️','🔥','⭐','🎉','💯','✅','❌','⚡'] },
  { name: '表情', emojis: ['😁','😅','😆','😉','😋','😎','😏','😒','😞','😔','😟','😕','🙁','😣','😖','😫','😩','🥺','😤','😠','😈','👿','💀','💩','🤡','👹','👺','👻','👽','🤖'] },
  { name: '手势', emojis: ['👋','🤚','🖐️','✋','🖖','👌','🤌','🤏','✌️','🤞','🤟','🤘','🤙','👈','👉','👆','👇','☝️','👍','👎','✊','👊','🤛','🤜','👏','🙌','👐','🤲','🤝','🙏'] },
  { name: '动物', emojis: ['🐶','🐱','🐭','🐹','🐰','🦊','🐻','🐼','🐨','🐯','🦁','🐮','🐷','🐸','🐵','🐔','🐧','🐦','🦅','🦆','🦉','🐺','🐗','🐴','🦄','🐝','🐛','🦋','🐌','🐞'] },
  { name: '食物', emojis: ['🍎','🍐','🍊','🍋','🍌','🍉','🍇','🍓','🫐','🍈','🍒','🍑','🥭','🍍','🥥','🥝','🍅','🥑','🍆','🌽','🌶️','🥕','🧅','🍔','🍟','🍕','🌭','🥪','🌮','🍜'] },
  { name: '自然', emojis: ['🌸','🌺','🌻','🌹','🌷','🌼','💐','🌾','🍀','🌿','🌲','🌳','🌴','🌵','🍁','🍂','🍃','🌈','☀️','🌤️','⛅','🌥️','☁️','🌧️','❄️','💧','🌊','🔥','⭐','🌙'] },
  { name: '物品', emojis: ['📧','📩','📨','📮','📝','📄','📅','📌','📎','✏️','🖊️','🖋️','💻','📱','☎️','💡','🔑','🔒','🎁','🎀','🎈','🎊','🏆','🥇','🎵','🎶','🎸','🎮','🎯','🚀'] },
];

/** 插入 emoji 到编辑器光标位置 */
function insertEmoji(emoji: string) {
  if (!editor.value) return;
  editor.value.chain().focus().insertContent(emoji).run();
  showEmojiPicker.value = false;
}

/** 点击外部关闭 emoji 选择器 */
function handleClickOutside(e: MouseEvent) {
  const target = e.target as HTMLElement;
  if (!target.closest('.emoji-dropdown')) {
    showEmojiPicker.value = false;
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside);
});

onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutside);
});

// 工具栏按钮配置
const fontSizes = ['12px', '14px', '16px', '18px', '20px', '24px', '28px', '32px'];
const lineHeightOptions = [
  { label: '1.0', value: '1' },
  { label: '1.2', value: '1.2' },
  { label: '1.5', value: '1.5' },
  { label: '1.8', value: '1.8' },
  { label: '2.0', value: '2' },
  { label: '2.5', value: '2.5' },
  { label: '3.0', value: '3' },
];
const fontFamilies = [
  { label: '默认', value: '' },
  { label: '宋体', value: 'SimSun, serif' },
  { label: '黑体', value: 'SimHei, sans-serif' },
  { label: '微软雅黑', value: 'Microsoft YaHei, sans-serif' },
  { label: '楷体', value: 'KaiTi, serif' },
  { label: 'Arial', value: 'Arial, sans-serif' },
  { label: 'Times New Roman', value: 'Times New Roman, serif' },
  { label: 'Courier New', value: 'Courier New, monospace' },
];
const presetColors = ['#000000', '#333333', '#666666', '#999999', '#ff3b30', '#ff9500', '#ffcc00', '#34c759', '#007aff', '#5856d6', '#af52de', '#ff2d55'];

// 当前文字颜色（用于颜色按钮指示器）
const currentColor = computed(() => editor.value?.getAttributes('textStyle').color || '#000000');

// 当前字体名称（用于字体按钮显示）
const currentFontLabel = computed(() => {
  const family = editor.value?.getAttributes('textStyle').fontFamily as string | undefined;
  if (!family) return '字体';
  const match = fontFamilies.find(f => f.value && family.startsWith(f.value.split(',')[0].trim()));
  return match ? match.label : family.split(',')[0].trim();
});

// 当前字号（用于字号按钮显示）
const currentSizeLabel = computed(() => {
  const size = editor.value?.getAttributes('textStyle').fontSize as string | undefined;
  return size || '字号';
});

/**
 * 下拉菜单操作函数
 * 核心流程：focus → 恢复 storedMarks → 执行样式命令
 * 解决：点击下拉菜单时编辑器失焦，storedMarks 被清除，
 * 导致后续输入不会应用已选中的字体/字号/颜色等样式
 */
const applyFontSize = (size: string | null) => {
  const e = editor.value;
  if (!e) return;
  e.commands.focus();
  restoreStoredMarks(e);
  if (size) {
    e.commands.setFontSize(size);
  } else {
    e.commands.unsetFontSize();
  }
};

const applyFontFamily = (family: string | null) => {
  const e = editor.value;
  if (!e) return;
  e.commands.focus();
  restoreStoredMarks(e);
  if (family) {
    e.commands.setFontFamily(family);
  } else {
    e.commands.unsetFontFamily();
  }
};

const applyLineHeight = (height: string | null) => {
  const e = editor.value;
  if (!e) return;
  e.commands.focus();
  restoreStoredMarks(e);
  if (height) {
    e.commands.setLineHeight(height);
  } else {
    e.commands.unsetLineHeight();
  }
};

const applyColor = (color: string | null) => {
  const e = editor.value;
  if (!e) return;
  e.commands.focus();
  restoreStoredMarks(e);
  if (color) {
    e.commands.setColor(color);
  } else {
    e.commands.unsetColor();
  }
};

const toolbarButtons = [
  {
    name: 'fontFamily',
    title: '字体',
    isDropdown: true,
    dropdownType: 'fontFamily',
  },
  {
    name: 'fontSize',
    title: '字号',
    isDropdown: true,
    dropdownType: 'fontSize',
  },
  {
    name: 'color',
    title: '文字颜色',
    isDropdown: true,
    dropdownType: 'color',
  },
  {
    name: 'bold',
    title: '加粗',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M6 4h8a4 4 0 0 1 4 4 4 4 0 0 1-4 4H6z"/><path d="M6 12h9a4 4 0 0 1 4 4 4 4 0 0 1-4 4H6z"/></svg>',
    action: () => editor.value?.chain().focus().toggleBold().run(),
    isActive: () => editor.value?.isActive('bold'),
  },
  {
    name: 'italic',
    title: '斜体',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="19" y1="4" x2="10" y2="4"/><line x1="14" y1="20" x2="5" y2="20"/><line x1="15" y1="4" x2="9" y2="20"/></svg>',
    action: () => editor.value?.chain().focus().toggleItalic().run(),
    isActive: () => editor.value?.isActive('italic'),
  },
  {
    name: 'underline',
    title: '下划线',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 3v7a6 6 0 0 0 12 0V3"/><line x1="4" y1="21" x2="20" y2="21"/></svg>',
    action: () => editor.value?.chain().focus().toggleUnderline().run(),
    isActive: () => editor.value?.isActive('underline'),
  },
  {
    name: 'strike',
    title: '删除线',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M16 4H9a3 3 0 0 0-3 3c0 1.5 1 2.5 2.5 3"/><line x1="4" y1="12" x2="20" y2="12"/><path d="M15 12c1.5.5 3 1.5 3 3a3 3 0 0 1-3 3H8"/></svg>',
    action: () => editor.value?.chain().focus().toggleStrike().run(),
    isActive: () => editor.value?.isActive('strike'),
  },
  {
    name: 'h1',
    title: '标题1',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 12h8"/><path d="M4 18V6"/><path d="M12 18V6"/><path d="M17 12l2-2v8"/></svg>',
    action: () => editor.value?.chain().focus().toggleHeading({ level: 1 }).run(),
    isActive: () => editor.value?.isActive('heading', { level: 1 }),
  },
  {
    name: 'h2',
    title: '标题2',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 12h8"/><path d="M4 18V6"/><path d="M12 18V6"/><path d="M18 12c1.5-1.5 3-2 3-3.5a2 2 0 0 0-4 0"/><path d="M17 16h4"/></svg>',
    action: () => editor.value?.chain().focus().toggleHeading({ level: 2 }).run(),
    isActive: () => editor.value?.isActive('heading', { level: 2 }),
  },
  {
    name: 'bulletList',
    title: '无序列表',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><circle cx="4" cy="6" r="1" fill="currentColor"/><circle cx="4" cy="12" r="1" fill="currentColor"/><circle cx="4" cy="18" r="1" fill="currentColor"/></svg>',
    action: () => editor.value?.chain().focus().toggleBulletList().run(),
    isActive: () => editor.value?.isActive('bulletList'),
  },
  {
    name: 'orderedList',
    title: '有序列表',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="10" y1="6" x2="21" y2="6"/><line x1="10" y1="12" x2="21" y2="12"/><line x1="10" y1="18" x2="21" y2="18"/><text x="2" y="8" font-size="8" fill="currentColor" stroke="none">1</text><text x="2" y="14" font-size="8" fill="currentColor" stroke="none">2</text><text x="2" y="20" font-size="8" fill="currentColor" stroke="none">3</text></svg>',
    action: () => editor.value?.chain().focus().toggleOrderedList().run(),
    isActive: () => editor.value?.isActive('orderedList'),
  },
  {
    name: 'blockquote',
    title: '引用',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M4.583 17.321C3.553 16.227 3 15 3 13.011c0-3.5 2.457-6.637 6.03-8.188l.893 1.378c-3.335 1.804-3.987 4.145-4.247 5.621.537-.278 1.24-.375 1.929-.311C9.591 11.689 11 13.166 11 15c0 1.933-1.567 3.5-3.5 3.5-1.22 0-2.36-.598-2.917-1.179zm10 0C13.553 16.227 13 15 13 13.011c0-3.5 2.457-6.637 6.03-8.188l.893 1.378c-3.335 1.804-3.987 4.145-4.247 5.621.537-.278 1.24-.375 1.929-.311C19.591 11.689 21 13.166 21 15c0 1.933-1.567 3.5-3.5 3.5-1.22 0-2.36-.598-2.917-1.179z"/></svg>',
    action: () => editor.value?.chain().focus().toggleBlockquote().run(),
    isActive: () => editor.value?.isActive('blockquote'),
  },
  {
    name: 'indent',
    title: '增加缩进',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="1" y1="4" x2="15" y2="4"/><line x1="1" y1="9" x2="15" y2="9"/><line x1="1" y1="14" x2="15" y2="14"/><line x1="1" y1="19" x2="15" y2="19"/><path d="M19 8l4 4-4 4"/></svg>',
    action: () => {
      const e = editor.value;
      if (!e) return;
      if (e.isActive('listItem')) {
        // 列表项缩进
        (e.chain().focus() as any).sinkListItem('listItem').run();
      } else if (e.isActive('paragraph')) {
        // 段落缩进：增加 indent 属性
        const currentIndent = (e.getAttributes('paragraph').indent as number) || 0;
        e.chain().focus().updateAttributes('paragraph', { indent: currentIndent + 1 }).run();
      }
    },
    isActive: () => false,
  },
  {
    name: 'outdent',
    title: '减少缩进',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="1" y1="4" x2="15" y2="4"/><line x1="1" y1="9" x2="15" y2="9"/><line x1="1" y1="14" x2="15" y2="14"/><line x1="1" y1="19" x2="15" y2="19"/><path d="M23 8l-4 4 4 4"/></svg>',
    action: () => {
      const e = editor.value;
      if (!e) return;
      if (e.isActive('listItem')) {
        // 列表项减少缩进
        (e.chain().focus() as any).liftListItem('listItem').run();
      } else if (e.isActive('paragraph')) {
        // 段落减少缩进
        const currentIndent = (e.getAttributes('paragraph').indent as number) || 0;
        if (currentIndent > 0) {
          e.chain().focus().updateAttributes('paragraph', { indent: currentIndent - 1 }).run();
        }
      }
    },
    isActive: () => false,
  },
  {
    name: 'lineHeight',
    title: '行间距',
    isDropdown: true,
    dropdownType: 'lineHeight',
  },
  {
    name: 'hr',
    title: '插入分割线',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="2" y1="12" x2="22" y2="12"/></svg>',
    action: () => editor.value?.chain().focus().setHorizontalRule().run(),
    isActive: () => false,
  },
  {
    name: 'table',
    title: '表格',
    isDropdown: true,
    dropdownType: 'table',
  },
  {
    name: 'link',
    title: '插入链接',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
    action: () => {
      const url = window.prompt('输入链接地址:', 'https://');
      if (url) {
        editor.value?.chain().focus().setLink({ href: url }).run();
      }
    },
    isActive: () => editor.value?.isActive('link'),
  },
  {
    name: 'image',
    title: '插入图片',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>',
    action: () => {
      const url = window.prompt('输入图片地址:', 'https://');
      if (url) {
        editor.value?.chain().focus().setImage({ src: url }).run();
      }
    },
    isActive: () => false,
  },
];
</script>

<style scoped>
.tiptap-editor {
  border: 1px solid var(--border-color);
  border-radius: var(--border-radius-lg, 8px);
  overflow: hidden;
  background: var(--bg-primary);
}

.editor-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-secondary);
}

.toolbar-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
}

.toolbar-btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.toolbar-btn.active {
  background: var(--accent-blue, #007AFF);
  color: #fff;
}

/* 下拉菜单 */
.toolbar-dropdown {
  position: relative;
}

.toolbar-dropdown > .toolbar-btn {
  width: auto;
  padding: 0 10px;
  font-size: 12px;
  gap: 2px;
}

.toolbar-dropdown > .toolbar-btn small {
  font-size: 10px;
  opacity: 0.6;
}

/* 图标+下拉箭头布局 */
.dropdown-icon-label {
  display: flex;
  align-items: center;
  gap: 2px;
}

.dropdown-menu {
  display: none;
  position: absolute;
  top: 100%;
  left: 0;
  z-index: 50;
  min-width: 120px;
  padding: 4px;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
}

.toolbar-dropdown:hover .dropdown-menu,
.toolbar-dropdown:focus-within .dropdown-menu {
  display: block;
}

.dropdown-item {
  display: block;
  width: 100%;
  padding: 6px 10px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--text-primary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  white-space: nowrap;
}

.dropdown-item:hover {
  background: var(--bg-hover);
}

/* 颜色选择器 */
.color-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 4px;
  padding: 6px;
}

.color-swatch {
  width: 22px;
  height: 22px;
  border: 2px solid transparent;
  border-radius: 4px;
  cursor: pointer;
  transition: transform 0.1s;
}

.color-swatch:hover {
  transform: scale(1.2);
  border-color: var(--text-primary);
}

/* 颜色按钮指示器 */
.color-btn-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
}

.color-indicator {
  width: 16px;
  height: 3px;
  border-radius: 1px;
}

.editor-content {
  min-height: 400px;
  max-height: none;
  overflow-y: auto;
  padding: 12px 16px;
}

/* Tiptap 编辑器内部样式 */
.editor-content :deep(.tiptap) {
  outline: none;
  min-height: 380px;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-primary);
}

.editor-content :deep(.tiptap p.is-editor-empty:first-child::before) {
  content: attr(data-placeholder);
  float: left;
  color: var(--text-tertiary);
  pointer-events: none;
  height: 0;
}

.editor-content :deep(.tiptap h1) { font-size: 1.5em; font-weight: 700; margin: 0.5em 0; }
.editor-content :deep(.tiptap h2) { font-size: 1.3em; font-weight: 600; margin: 0.4em 0; }
.editor-content :deep(.tiptap h3) { font-size: 1.1em; font-weight: 600; margin: 0.3em 0; }
.editor-content :deep(.tiptap ul) { padding-left: 1.5em; list-style: disc; }
.editor-content :deep(.tiptap ol) { padding-left: 1.5em; list-style: decimal; }
.editor-content :deep(.tiptap blockquote) {
  border-left: 3px solid var(--accent-blue, #007AFF);
  padding-left: 1em;
  margin: 0.5em 0;
  color: var(--text-secondary);
}
.editor-content :deep(.tiptap a) { color: var(--accent-blue, #007AFF); text-decoration: underline; }
.editor-content :deep(.tiptap img) { max-width: 100%; border-radius: 4px; }

/* 分割线样式 */
.editor-content :deep(.tiptap hr) {
  border: none;
  border-top: 2px solid var(--border-color);
  margin: 16px 0;
}

/* 表格样式 */
.editor-content :deep(.tiptap table) {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
  overflow: hidden;
  table-layout: fixed;
}
.editor-content :deep(.tiptap table td),
.editor-content :deep(.tiptap table th) {
  border: 1px solid var(--border-color);
  padding: 6px 8px;
  min-width: 60px;
  vertical-align: top;
  position: relative;
  text-align: left;
  box-sizing: border-box;
}
.editor-content :deep(.tiptap table th) {
  background: var(--bg-secondary);
  font-weight: 600;
}
.editor-content :deep(.tiptap table .selectedCell) {
  background: rgba(0, 122, 255, 0.1);
}
.editor-content :deep(.tiptap table .column-resize-handle) {
  position: absolute;
  right: -2px;
  top: 0;
  bottom: -2px;
  width: 4px;
  background-color: var(--accent-blue, #007AFF);
  pointer-events: none;
}

/* 下拉菜单分隔线 */
.dropdown-divider {
  height: 1px;
  background: var(--border-color);
  margin: 4px 0;
}

/* 下拉菜单危险操作（删除） */
.dropdown-item.danger {
  color: #ff3b30;
}
.dropdown-item.danger:hover {
  background: #fff1f0;
}

/* 下拉菜单禁用状态 */
.dropdown-item:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.dropdown-item:disabled:hover {
  background: transparent;
}

/* Emoji 选择器 */
.emoji-dropdown {
  position: relative;
}

.emoji-picker {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 4px;
  width: 340px;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15);
  z-index: 100;
  padding: 8px;
}

.emoji-tabs {
  display: flex;
  gap: 2px;
  margin-bottom: 6px;
  border-bottom: 1px solid var(--border-color);
  padding-bottom: 4px;
  overflow-x: auto;
}

.emoji-tab {
  padding: 3px 10px;
  border: none;
  background: none;
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  border-radius: 4px;
  transition: all 0.15s;
  white-space: nowrap;
}

.emoji-tab:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.emoji-tab.active {
  background: var(--accent-blue, #007AFF);
  color: #fff;
}

.emoji-grid {
  display: grid;
  grid-template-columns: repeat(10, 1fr);
  gap: 2px;
  max-height: 240px;
  overflow-y: auto;
}

.emoji-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  background: none;
  font-size: 18px;
  cursor: pointer;
  border-radius: 4px;
  transition: background 0.1s;
  padding: 0;
  line-height: 1;
}

.emoji-btn:hover {
  background: var(--bg-hover);
}

/* ==================== 手机端 emoji 选择器覆盖 ==================== */
@media (max-width: 768px) {
  .emoji-picker {
    position: fixed !important;
    left: 0 !important;
    right: 0 !important;
    bottom: 0 !important;
    top: auto !important;
    width: 100% !important;
    max-height: 45vh !important;
    margin-top: 0 !important;
    border-radius: 12px 12px 0 0 !important;
    z-index: 1000 !important;
    padding: 10px 12px 16px !important;
    box-shadow: 0 -4px 20px rgba(0, 0, 0, 0.15) !important;
  }

  .emoji-grid {
    max-height: calc(45vh - 60px) !important;
  }
}
</style>
