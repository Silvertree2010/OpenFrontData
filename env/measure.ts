import fs from "fs"; import path from "path"; import { fileURLToPath } from "url";
import { Config } from "../vendor/openfront/src/core/configuration/Config";
import { Executor } from "../vendor/openfront/src/core/execution/ExecutionManager";
import { Game, PlayerInfo, PlayerType } from "../vendor/openfront/src/core/game/Game";
import { createGame } from "../vendor/openfront/src/core/game/GameImpl";
import { createNationsForGame } from "../vendor/openfront/src/core/game/NationCreation";
import { loadTerrainMap } from "../vendor/openfront/src/core/game/TerrainMapLoader";
import { GameRunner } from "../vendor/openfront/src/core/GameRunner";
import { PseudoRandom } from "../vendor/openfront/src/core/PseudoRandom";
import { GameRecord, GameRecordSchema, GameStartInfo } from "../vendor/openfront/src/core/Schemas";
import { decompressGameRecord, simpleHash, toWireGameStartInfo } from "../vendor/openfront/src/core/Util";
import { NodeGameMapLoader } from "../vendor/openfront/tests/perf/fullgame/NodeGameMapLoader";
import { ObsEncoder, NUM_CHANNELS } from "./obs";
import zlib from "zlib";
const ENGINE = path.join(path.dirname(fileURLToPath(import.meta.url)), "../vendor/openfront");
async function main(){
  const file=process.argv[2]; console.debug=()=>{};
  const raw=JSON.parse(fs.readFileSync(file,"utf8")); const pp=GameRecordSchema.safeParse(raw);
  const rec:GameRecord=decompressGameRecord(pp.success?pp.data:(raw as GameRecord)); const info=rec.info;
  const gs:GameStartInfo=toWireGameStartInfo({gameID:info.gameID,lobbyCreatedAt:info.lobbyCreatedAt,config:info.config,players:info.players,tribes:info.tribes});
  const cfg=new Config(info.config,null,false);
  const terrain=await loadTerrainMap(info.config.gameMap,info.config.gameMapSize,new NodeGameMapLoader(path.join(ENGINE,"resources/maps")),false);
  const rnd=new PseudoRandom(simpleHash(gs.gameID));
  const humans=gs.players.map(x=>new PlayerInfo(x.username,PlayerType.Human,x.clientID,rnd.nextID(),x.isLobbyCreator??false,x.clanTag,x.friends??[],x.teamIndex??null));
  const nations=createNationsForGame(gs,terrain.nations,terrain.additionalNations,humans.length,rnd);
  const game:Game=createGame(humans,nations,terrain.gameMap,terrain.miniGameMap,cfg,terrain.teamGameSpawnAreas);
  let fatal:string|undefined; const runner=new GameRunner(game,new Executor(game,gs.gameID,undefined,gs.tribes?.map(t=>t.name)),(gu)=>{if("errMsg" in gu)fatal=gu.errMsg;}); runner.init();
  const enc=new ObsEncoder(180,90); enc.initTerrain(game);
  const n=180*90; const u8=new Uint8Array(NUM_CHANNELS*n); const out=new Float32Array(NUM_CHANNELS*n);
  const samples:number[][]=[]; // [tick, rawUint8, zstdBytes]
  const marks=new Set([400,1200,2500,5000,9000]);
  for(const t of rec.turns){ runner.addTurn(t); runner.executeNextTick(); if(fatal){console.error(fatal);process.exit(1);}
    if(marks.has(game.ticks())){
      enc.scanTick(game);
      const pl=game.players().filter(x=>x.isAlive()&&x.type()===PlayerType.Human).sort((a,b)=>b.numTilesOwned()-a.numTilesOwned())[0];
      if(!pl) continue;
      enc.encodeMap(pl,new Set(pl.allies().map(a=>a.smallID())),out);
      // quantisieren: [-1,1]→[0,255]
      for(let i=0;i<out.length;i++){ let v=out[i]; if(v<-1)v=-1; if(v>1)v=1; u8[i]=Math.round((v+1)*127.5); }
      const z=zlib.zstdCompressSync ? zlib.zstdCompressSync(Buffer.from(u8)) : zlib.gzipSync(Buffer.from(u8),{level:9});
      samples.push([game.ticks(), u8.length, z.length]);
    }
    if(game.ticks()>9000) break;
  }
  console.log("Karte", game.width()+"x"+game.height(), "| roh uint8:", (NUM_CHANNELS*n/1024).toFixed(0)+"KB");
  for(const [t,r,z] of samples) console.log(`  Tick ${t}: zstd ${(z/1024).toFixed(1)}KB (Faktor ${(r/z).toFixed(0)}x)`);
  const avg=samples.reduce((a,s)=>a+s[2],0)/Math.max(1,samples.length);
  console.log(`  Ø ${(avg/1024).toFixed(1)}KB/Sample → Hochrechnung:`);
  for(const m of [2,4,18.4]) console.log(`    ${m} Mio Samples: ${(avg*m*1e6/1e9).toFixed(0)} GB`);
}
main();
