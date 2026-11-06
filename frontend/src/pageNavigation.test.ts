import assert from 'node:assert/strict'
import { test } from 'node:test'
import {clampPage,sourceSlideIndex,exportPdfPageNumber,pageAtTime} from './pageNavigation.ts'
import type {PageAudio} from './api.ts'

test('Overview has no source index; all eleven source slides map after it',()=>{
  assert.equal(sourceSlideIndex(0),null)
  assert.equal(exportPdfPageNumber(0),1)
  for(let page=1;page<=11;page++){
    assert.equal(sourceSlideIndex(page),page-1)
    assert.equal(exportPdfPageNumber(page),page+1)
  }
  assert.equal(clampPage(-1,11),0);assert.equal(clampPage(12,11),11)
})
const audio={pages:[
  {page:3,audio_ranges:[{start:1,end:70,confidence:.1}]},
  {page:4,audio_ranges:[{start:78.28,end:108.38,confidence:1}]},
  {page:5,audio_ranges:[{start:112.2,end:148.71,confidence:.58}]},
]} as PageAudio

test('audio following uses source slide numbers without an overview offset',()=>{
  assert.equal(pageAtTime(audio,80),4)
  assert.equal(pageAtTime(audio,115),5)
  assert.equal(pageAtTime(audio,10),null)
  assert.equal(pageAtTime(audio,108.38),null)
})
test('Overview navigation does not rewrite source audio timestamps',()=>{
  const source=audio.pages[1]
  const before=JSON.stringify(source)
  assert.equal(exportPdfPageNumber(source.page),5)
  assert.equal(source.audio_ranges[0].start,78.28)
  assert.equal(JSON.stringify(source),before)
})
