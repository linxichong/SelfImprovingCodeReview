# AACR-Bench による品質評価

公開ベンチマークには、専門家が検証した AACR-Bench の参照指摘を使用する。上流のラベルをそのまま用い、
本プロジェクトの正解集合として承認・発行する操作は不要とする。

1. [上流の評価手順](https://github.com/alibaba/aacr-bench/tree/main/evaluation)に従ってデータを準備する。
   上流のコミット SHA、データの SHA-256、評価対象の `instance_id` と base・head SHA を記録する。
2. 各サンプルの head をチェックアウトした評価用リポジトリを指定してレビューする。
   `uv run --frozen review review --repository <REPO> --config <CONFIG> --range <BASE> <HEAD> --output <JSON>`
   モデル、プロンプト、ルール、メモリの条件を比較間で固定し、参照指摘はレビュー入力から除く。
3. 本プロジェクトの結果 JSON を、固定した評価器が受け付ける形式に変換する。リポジトリには現時点で専用の変換器がない。
   [上流の指標定義](https://github.com/alibaba/aacr-bench/blob/main/docs/metrics.md)を参照し、
   同じ採点方式と判定モデルで比較する。模擬判定は動作確認に使用する。
4. 対象件数、完了・失敗・未実行件数、Precision、Recall、F1、Token、時間、取得可能な費用を記録する。
   欠落した結果と、正常に完了して指摘がなかった結果を区別する。

公開スコアは既存の A/B/C 評価と区別して報告する。既知のラベルや結果を調整に使ったサンプルは、
独立した未見の評価根拠に使用しない。独立性の記録は[評価データの運用](评估数据_独立性管理.md)を参照する。
