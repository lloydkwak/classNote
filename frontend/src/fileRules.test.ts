import assert from 'node:assert/strict'
import { test } from 'node:test'
import { validateFile,PDF_LIMIT,AUDIO_LIMIT,fileSize } from './fileRules.ts'

test('accepts supported PDF and audio formats, case insensitive',()=>{
  assert.equal(validateFile('slides.PDF',128,'pdf'),null)
  for(const ext of ['m4a','wav','mp3','ogg','flac','aac','mp4','webm','opus'])assert.equal(validateFile(`lecture.${ext}`,100,'audio'),null)
})
test('rejects unsupported files, empty files and sizes over the limits',()=>{
  assert.ok(validateFile('slides.exe',128,'pdf'))
  assert.ok(validateFile('lecture.txt',128,'audio'))
  assert.ok(validateFile('empty.pdf',0,'pdf'))
  assert.ok(validateFile('large.pdf',PDF_LIMIT+1,'pdf'))
  assert.ok(validateFile('large.wav',AUDIO_LIMIT+1,'audio'))
  assert.equal(validateFile('max.pdf',PDF_LIMIT,'pdf'),null)
  assert.equal(validateFile('max.wav',AUDIO_LIMIT,'audio'),null)
})
test('formats actual file sizes without progress estimates',()=>{
  assert.equal(fileSize(2048),'2 KB');assert.equal(fileSize(1048576),'1.0 MB')
})
