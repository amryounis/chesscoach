# Answer-key evaluation

| Key | Run | Recall | False claims | Board errors | Caught | Removed | Note |
|---|---|---|---|---|---|---|---|
| homer-8-bg4 | pipeline · qwen3:14b | 1/2 | 0 | 0 | 0 | 0 | missed: f-file |
| homer-9-dxc4 | pipeline · qwen3:14b | 2/2 | 0 | 0 | 0 | 0 |  |
| homer-12-kxf7 | pipeline · qwen3:14b | 0/1 | 0 | 0 | 0 | 0 | missed: bishop-pair |
| rook-check | pipeline · qwen3:14b | 1/1 | 0 | 0 | 0 | 0 |  |
| test-9-nxd4 | pipeline · qwen3:14b | 2/3 | 0 | 0 | 0 | 0 | missed: removes-threat |

Total: recall 6/9, false claims 0, board errors 0 over 5 runs.

## 8...Bg4 — the bishop move gives up king shelter — pipeline · qwen3:14b

**Assessment**: Inaccuracy  
**What happened**: Black moves the bishop from c8 to g4. The move weakens Black's king position by reducing the number of shield pawns and exposing the king to potential future threats. While it gains some immediate activity by attacking the knight on f3 and the pawn on h5, it fails to secure long-term king safety, which is a critical positional factor in the middlegame.  

**Better was**: Moving the pawn from g6 to g5 would have preserved Black's king shelter and kept the king more protected, maintaining a more stable position.  

**What now**: White will capture the pawn on g6 with the pawn from h5, which is a direct response to the weakened position. Black should respond by capturing the pawn on g6 with the pawn from f7, aiming to recapture and maintain material balance while trying to neutralize White's initiative.

Missed: A file next to the king (the f-file) opens

## 9...dxc4 — the capture opens files next to the king — pipeline · qwen3:14b

**Assessment**: Inaccuracy  
**What happened**: Black captures White's pawn on c4 with the pawn from d5. This move weakens Black's king position by reducing the number of shield pawns around the king and opening up the d and f files, which are now vulnerable to White's future activity. It also fails to take advantage of the immediate tactical opportunity on the kingside.  

**Better was**: Capturing the pawn on h5 with the bishop from g4, which would have improved Black's king safety and created an immediate threat against White's knight on f3.  

**What now**: White will capture Black's pawn on g6 with the pawn from h5, which threatens to open the g-file and increase pressure on Black's king. Black should respond by capturing the pawn on g6 with the pawn from f7, which helps to defend against the immediate threat and maintain some control over the center.

## 12...Kxf7 — taking with the king costs the bishop pair — pipeline · qwen3:14b

**Assessment**: Inaccuracy  
**What happened**: Black captures White's pawn on f7 with the king from e8. Capturing the pawn on f7 with the king removes Black's only shield pawn, leaving the king completely exposed. This weakens Black's king safety significantly and allows White to exploit the open f-file, which is now a key tactical and strategic target.  

**Better was**: Moving the king to f8 instead, which maintains a shield pawn and avoids exposing the king while still attacking White's pawn on f7.  

**What now**: White will move the knight to e5, giving check. Black should respond by capturing the knight with the bishop on f6, which is the best way to neutralize the immediate threat and maintain active piece play.

Missed: After the engine's ...Kf8 Black keeps the bishop pair

## 18.Rhe1+ — White two pieces up, Black's king still in the centre — pipeline · qwen3:14b

**Assessment**: Excellent  
**What happened**: White moves the rook from h1 to e1, giving check. This move is excellent because it prevents Black from castling, which is a critical strategic goal for Black. By giving check, White also forces Black to move the king, reducing its mobility and limiting its future options.  

**What now**: Black will move the king to d8 to escape check. White should then advance the knight to e5, a strong central square that pressures Black’s position and supports the long-term plan of increasing control over the center.

## 9.Nxd4 — removes the threat to c3 and e3, but leaves the knight on f6 — pipeline · qwen3:14b

**Assessment**: Blunder  
**What happened**: White captures Black's pawn on d4 with the knight from f3. White's move removes key defenders from the e5 pawn, leaving it completely exposed and allowing Black to immediately recapture with a strong piece. The move also gives Black a significant material and positional advantage, as the engine's preferred choice would have preserved White's pawn structure and increased mobility.  

**Better was**: Capturing Black's knight on f6 with the pawn from e5, which would have been a counter-attack that preserves White's pawn structure and increases control over the center.  

**What now**: Black will immediately capture the hanging pawn on e5 with the knight from c6. White should respond by advancing the pawn from f2 to f4, aiming to develop the king's bishop and restrict Black's knight on g6.

Missed: Capturing on d4 removes the pawn that attacked c3 and e3
