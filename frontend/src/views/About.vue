<template>
  <div class="about-page">
    <section class="about-card">
      <div class="brand-row">
        <img :src="base + 'icon-full.png'" alt="FlyMail" class="brand-logo" @error="onLogoError" />
        <div class="brand-meta">
          <div class="brand-name-line">
            <span class="brand-name">Fly<span class="accent">Mail</span></span>
            <span class="ver">v{{ version }}</span>
          </div>
          <p class="brand-slogan">为多邮箱用户打造的自托管邮件客户端</p>
        </div>
      </div>

      <p class="brand-desc">
        FlyMail 统一管理 Gmail、Outlook、QQ 邮箱、网易邮箱、iCloud 等主流平台的邮件数据。
        当前版本已重构为 Docker 多用户版，支持管理员与普通用户分权、历史邮件后台同步、断点续传、
        附件与内嵌图片下载，以及本地数据目录持久化。
      </p>
    </section>

    <section class="about-card">
      <div class="pill-row">
        <span class="pill" v-for="feature in features" :key="feature">
          <span class="pill-dot"></span>{{ feature }}
        </span>
      </div>
      <div class="divider"></div>
      <div class="pill-row">
        <span class="pill tech" v-for="tech in techs" :key="tech">{{ tech }}</span>
      </div>
    </section>

    <section class="about-card">
      <div class="link-block">
        <h3>项目地址</h3>
        <a class="repo-link" href="https://github.com/wangjinjing1/flymail" target="_blank" rel="noreferrer">
          github.com/wangjinjing1/flymail
        </a>
      </div>

      <div class="link-block">
        <h3>致谢</h3>
        <p>
          感谢原作者
          <a class="inline-link" href="https://github.com/DinDing1/FlyMail" target="_blank" rel="noreferrer">
            DinDing1/FlyMail
          </a>
          。
        </p>
      </div>
    </section>

    <div class="footer">
      <span>© 2026 luisa · GNU GPLv3</span>
    </div>
  </div>
</template>

<script setup lang="ts">
const version = import.meta.env.VITE_APP_VERSION || '0.0.0';
const base = import.meta.env.BASE_URL;

function onLogoError(event: Event) {
  (event.target as HTMLImageElement).style.display = 'none';
}

const features = ['多邮箱', '实时同步', '自托管隐私', '同步管理', '断点续传', '多用户隔离'];
const techs = ['Vue 3', 'TypeScript', 'FastAPI', 'MySQL', 'IMAP', 'WebSocket', 'Docker'];
</script>

<style scoped>
.about-page {
  flex: 1;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  padding: 24px;
  background: var(--bg-secondary);
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.about-card {
  background: var(--bg-card);
  border-radius: var(--border-radius-lg);
  box-shadow: var(--shadow-card);
  padding: 20px 24px;
}

.brand-row {
  display: flex;
  align-items: center;
  gap: 16px;
}

.brand-logo {
  width: 56px;
  height: 56px;
  border-radius: 14px;
  box-shadow: var(--shadow-sm);
  flex-shrink: 0;
}

.brand-meta {
  flex: 1;
  min-width: 0;
}

.brand-name-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

.brand-name {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
}

.accent {
  color: var(--color-accent);
}

.ver {
  font-size: 12px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}

.brand-slogan {
  font-size: 14px;
  color: var(--text-tertiary);
  margin: 4px 0 0;
}

.brand-desc {
  font-size: 14px;
  color: var(--text-secondary);
  line-height: 1.75;
  margin: 16px 0 0;
  padding-top: 16px;
  border-top: 1px solid var(--border-color);
}

.pill-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: var(--bg-hover);
  border-radius: var(--border-radius-full);
  font-size: 12px;
  color: var(--text-secondary);
  font-weight: 500;
}

.pill-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-accent);
}

.pill.tech {
  background: var(--bg-secondary);
  color: var(--text-tertiary);
}

.divider {
  height: 1px;
  background: var(--border-color);
  margin: 16px 0;
}

.link-block + .link-block {
  margin-top: 18px;
}

.link-block h3 {
  margin: 0 0 10px;
  font-size: 15px;
  color: var(--text-primary);
}

.link-block p {
  margin: 0;
  font-size: 14px;
  color: var(--text-secondary);
  line-height: 1.7;
}

.repo-link,
.inline-link {
  color: var(--color-accent);
  text-decoration: none;
  word-break: break-all;
}

.repo-link:hover,
.inline-link:hover {
  text-decoration: underline;
}

.footer {
  text-align: center;
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 8px 0;
  margin-top: auto;
  opacity: 0.75;
}

@media (max-width: 768px) {
  .about-page {
    padding: 16px;
  }

  .about-card {
    padding: 16px;
  }

  .brand-row {
    align-items: flex-start;
  }

  .brand-logo {
    width: 44px;
    height: 44px;
    border-radius: 12px;
  }

  .brand-name {
    font-size: 18px;
  }
}
</style>
