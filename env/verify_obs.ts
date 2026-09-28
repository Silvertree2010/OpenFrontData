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
import { ObsEncoder } from "./obs";
const ENGINE = path.join(path.dirname(fileURLToPath(import.meta.url)), "../vendor/openfront");
async function main(){
  const file=process.argv[2]; const until=Number(process.argv[3]||1500);
  console.debug=()=>{};
  const raw=JSON.parse(fs.readFileSync(file,"utf8"));
  const p=GameRecordSchema.safeParse(raw);
  const rec:GameRecord=decompressGameRecord(p.success?p.data:(raw as GameRecord)); const info=rec.info;
  const gs:GameStartInfo=toWireGameStartInfo({gameID:info.gameID,lobbyCreatedAt:info.lobbyCreatedAt,config:info.config,players:info.players,tribes:info.tribes});
  const cfg=new Config(info.config,null,false);
  const terrain=await loadTerrainMap(info.config.gameMap,info.config.gameMapSize,new NodeGameMapLoader(path.join(ENGINE,"resources/maps")),false);
  const rnd=new PseudoRandom(simpleHash(gs.gameID));
  const humans=gs.players.map(pp=>new PlayerInfo(pp.username,PlayerType.Human,pp.clientID,rnd.nextID(),pp.isLobbyCreator??false,pp.clanTag,pp.friends??[],pp.teamIndex??null));
  const nations=createNationsForGame(gs,terrain.nations,terrain.additionalNations,humans.length,rnd);
  const game:Game=createGame(humans,nations,terrain.gameMap,terrain.miniGameMap,cfg,terrain.teamGameSpawnAreas);
  let fatal:string|undefined; const runner=new GameRunner(game,new Executor(game,gs.gameID,undefined,gs.tribes?.map(t=>t.name)),(gu)=>{if("errMsg" in gu)fatal=gu.errMsg;}); runner.init();
  const enc=new ObsEncoder(180,90);
  for(const t of rec.turns){ runner.addTurn(t); runner.executeNextTick(); if(fatal){console.error(fatal);process.exit(1);} if(game.ticks()>=until) break; }
  const alive=game.players().filter(x=>x.isAlive()&&x.type()===PlayerType.Human);
  const pl=alive.sort((a,b)=>b.numTilesOwned()-a.numTilesOwned())[0];
  const v=enc.encodeVec(game,pl,24);
  console.log("Tick",game.ticks(),"| Spieler-Felder(neu):",
    "troopsRatio="+v.own.troopsRatio.toFixed(3),
    "boardShare="+v.own.boardShare.toFixed(4),
    "boatsOut="+v.own.boatsOut, "isLeader="+v.own.isLeader);
  const leaders=v.opponents.filter(o=>o.isLeader).length;
  const allied=v.opponents.filter(o=>o.allyTicksLeft>0);
  console.log("Gegner:",v.opponents.length,"| isLeader-Summe(soll 0/1)="+leaders,
    "| mit Allianz-Timer:",allied.length, allied.slice(0,3).map(o=>"t="+o.allyTicksLeft.toFixed(2)));
  console.log("  Silo-Besitzer:",v.opponents.filter(o=>o.hasSilo).length,"| SAM-Besitzer:",v.opponents.filter(o=>o.hasSam).length);
  const bad=v.opponents.filter(o=>o.allyTicksLeft<0||o.allyTicksLeft>1.01);
  console.log("  Timer ausserhalb [0,1]:",bad.length,"(soll 0)");
}
main();
