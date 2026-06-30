/**
 * 版本号同步脚本
 *
 * 从项目根目录的 VERSION 文件读取版本号，同步到：
 * - package.json（根目录）
 * - frontend/package.json
 *
 * 用法：node scripts/sync-version.js
 */

const fs = require('fs');
const path = require('path');

const rootDir = path.join(__dirname, '..');
const versionFile = path.join(rootDir, 'VERSION');

// 读取版本号
const version = fs.readFileSync(versionFile, 'utf-8').trim();
console.log(`同步版本号: ${version}`);

// 同步到根目录 package.json
const rootPkgPath = path.join(rootDir, 'package.json');
const rootPkg = JSON.parse(fs.readFileSync(rootPkgPath, 'utf-8'));
rootPkg.version = version;
fs.writeFileSync(rootPkgPath, JSON.stringify(rootPkg, null, 2) + '\n', 'utf-8');
console.log(`  ✓ package.json`);

// 同步到 frontend/package.json
const frontendPkgPath = path.join(rootDir, 'frontend', 'package.json');
const frontendPkg = JSON.parse(fs.readFileSync(frontendPkgPath, 'utf-8'));
frontendPkg.version = version;
fs.writeFileSync(frontendPkgPath, JSON.stringify(frontendPkg, null, 2) + '\n', 'utf-8');
console.log(`  ✓ frontend/package.json`);

console.log('版本号同步完成');
