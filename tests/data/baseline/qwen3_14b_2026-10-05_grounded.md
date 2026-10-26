# Answer-key evaluation

| Key | Run | Recall | False claims | Board errors | Removed | Note |
|---|---|---|---|---|---|---|
| homer-8-bg4 | pipeline · qwen3:14b | 2/2 | 0 | 0 | 0 |  |
| homer-9-dxc4 | pipeline · qwen3:14b | 2/2 | 0 | 0 | 0 |  |
| homer-12-kxf7 | pipeline · qwen3:14b | 0/1 | passed-pawn | 0 | 0 | missed: bishop-pair |
| rook-check | pipeline · qwen3:14b | 1/1 | 0 | 0 | 0 |  |
| test-9-nxd4 | pipeline · qwen3:14b | 3/3 | 0 | 0 | 0 |  |

Total: recall 8/9, false claims 1, board errors 0 over 5 runs.

## 8...Bg4 — the bishop move gives up king shelter — pipeline · qwen3:14b

**Assessment**: Inaccuracy  
**What happened**: Black moves the bishop from c8 to g4. The move weakens Black's king position by reducing the number of shield pawns and exposing the king to potential threats on the f-file. While it creates some immediate pressure on White's knight and pawn, it fails to account for the long-term loss of king safety and mobility.  

**Better was**: Moving the pawn from g6 to g5 would have preserved Black's king shelter and maintained better control over the central and kingside files.  

**What now**: White will capture the pawn on g6 with the pawn from h5, and Black should respond by capturing the pawn on g6 with the pawn from f7. This exchange keeps the position somewhat balanced, but Black is now at a disadvantage due to the earlier inaccuracy.

## 9...dxc4 — the capture opens files next to the king — pipeline · qwen3:14b

**Assessment**: Inaccuracy  
**What happened**: Black captures White's pawn on c4 with the pawn from d5. This move weakens Black's king position by reducing the number of shield pawns protecting the king and opening up the d and f files, which are now vulnerable. It also fails to take advantage of the immediate tactical opportunity on the kingside.  

**Better was**: Capturing the pawn on h5 with the bishop from g4, which would have created a direct attack on White's knight on f3 and improved Black's king safety by maintaining more shield pawns.  

**What now**: White will capture Black's pawn on g6 with the pawn from h5, putting pressure on Black's pawn on f7. Black should respond by capturing the pawn on g6 with the pawn from f7 to prevent further pressure and maintain a solid structure.

## 12...Kxf7 — taking with the king costs the bishop pair — pipeline · qwen3:14b

**Assessment**: Inaccuracy  
**What happened**: Black captures White's pawn on f7 with the king from e8. The move weakens Black's king position by removing the last shield pawn, leaving the king completely exposed. It also allows White to gain a structural advantage by creating a passed pawn on the f-file, which Black would struggle to contain.  

**Better was**: Moving the king to f8 instead, which preserves a shield pawn and avoids giving White the opportunity to create a passed pawn.  

**What now**: White will move the knight to e5, giving check. Black should respond by capturing the knight with the bishop on f6, which removes a key White piece and prevents further pressure on the king.

Missed: After the engine's ...Kf8 Black keeps the bishop pair

False claims: Kxf7 gains a passed pawn (the inversion the line comparison fixed)

## 18.Rhe1+ — White two pieces up, Black's king still in the centre — pipeline · qwen3:14b

**Assessment**: Excellent  
**What happened**: White moves the rook from h1 to e1, giving check. This move is excellent because it prevents Black from castling, which is a critical strategic advantage. By giving check, White also forces Black to move its king, reducing its king's safety and limiting its options for development.  

**What now**: Black will likely move its king to d8 to escape check. White should then play the knight to e5, a strong central square that exerts pressure on Black's position and supports the long-term plan of improving White's central control.

## 9.Nxd4 — removes the threat to c3 and e3, but leaves the knight on f6 — pipeline · qwen3:14b

**Assessment**: Blunder  
**What happened**: White captures Black's pawn on d4 with the knight from f3. This move is a serious mistake because it leaves the White pawn on e5 completely undefended, which is immediately captured by Black's knight on c6. In comparison, the engine's choice would have preserved White's pawn structure and increased White's material advantage, giving White a much stronger position.  

**Better was**: Capturing Black's knight on f6 with the pawn from e5, which would have been a counter-attack that removes the threat to the knight on c3 and increases White's material advantage.  

**What now**: Black will immediately capture the hanging pawn on e5 with the knight from c6. White should respond by advancing the pawn from f2 to f4, which helps control the center and supports the knight on d4.
