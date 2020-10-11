const path = require('path');
const sh = require('shelljs');

sh.set('-e');

const srcPath = '../dist/*';
const dstPath = 'dist/';

if (!sh.test('-e', path.dirname(srcPath))) {
  console.error(`${srcPath} not found`);
  process.exit(-1);
}
const files = sh.ls(srcPath);
if (!files.length) {
  console.error(`${srcPath} is empty`);
  process.exit(-1);
}

sh.rm('-rf', dstPath);
sh.mkdir(dstPath);

console.log('copy files', files.map(x => x.toString()));
sh.cp('-R', srcPath, dstPath);
