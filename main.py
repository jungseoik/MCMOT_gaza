"""MOT evaluation entry point.

Dispatches to one of three backends:
  - torch    : original PyTorch FP16 detector + PyTorch ReID
  - trt      : TensorRT detector + TensorRT ReID (basic)
  - trt_opt  : TensorRT FP16 detector + GPU-optimized ReID preprocessing

All three share dataset loading, MOT-format result saving, post-processing,
and timing measurement (see src/eval_common.py). Output is written under
results/trackers/{MOT17,MOT20}-val/<exp_name>/data/<seq>.txt and the timing
summary is also persisted as <exp_name>_timing.json next to it.
"""

import json
import os
import time

from args import make_parser
import assets  # noqa: F401  (registers asset helpers used by the tracker)

from src.eval_common import (
    apply_post_processing,
    print_metrics_summary,
    print_timing_summary,
    run_trackeval,
    save_mot_results,
)


def get_main_args():
    parser = make_parser()
    parser.add_argument("--dataset", type=str, default="mot17",
                        choices=["mot17", "mot20", "custom"])
    parser.add_argument("--result_folder", type=str, default="results/trackers/")
    parser.add_argument("--test_dataset", action="store_true")
    parser.add_argument("--exp_name", type=str, default="test")
    parser.add_argument("--no_reid", action="store_true",
                        help="mark if visual embedding should NOT be used")
    parser.add_argument("--no_cmc", action="store_true",
                        help="mark if camera motion compensation should NOT be used")

    parser.add_argument("--s_sim_corr", action="store_true",
                        help="use the corrected shape similarity calculation")

    parser.add_argument("--btpp_arg_iou_boost", action="store_true",
                        help="BoostTrack++: only IoU is used for detection confidence boost")
    parser.add_argument("--btpp_arg_no_sb", action="store_true",
                        help="BoostTrack++: disable soft detection confidence boost")
    parser.add_argument("--btpp_arg_no_vt", action="store_true",
                        help="BoostTrack++: disable varying threshold")

    parser.add_argument("--no_post", action="store_true",
                        help="skip DTI + GBI post-processing")
    parser.add_argument("--no_eval", action="store_true",
                        help="skip TrackEval benchmark scoring")
    parser.add_argument("--eval_target", type=str, default="post_gbi",
                        choices=["raw", "post", "post_gbi"],
                        help="which result variant to feed into TrackEval")

    parser.add_argument("--engine", type=str, default="torch",
                        choices=["torch", "trt", "trt_opt"],
                        help="evaluation backend (torch | trt | trt_opt)")
    parser.add_argument("--yolox_engine", type=str,
                        default="external/weights/trt/yolox_mot20_fp16.engine",
                        help="YOLOX TensorRT engine path (used by trt / trt_opt)")
    parser.add_argument("--reid_engine", type=str,
                        default="external/weights/trt/fastreid_sbs_s50_fp16.engine",
                        help="FastReID TensorRT engine path (used by trt / trt_opt)")
    parser.add_argument("--detector_weights", type=str, default=None,
                        help="override default_settings detector path (torch backend)")
    parser.add_argument("--input_size", type=int, nargs=2, default=None,
                        metavar=("H", "W"),
                        help="override default_settings input size, e.g. --input_size 896 1600")

    args = parser.parse_args()

    if args.dataset == "mot17":
        args.result_folder = os.path.join(args.result_folder, "MOT17-val")
    elif args.dataset == "mot20":
        args.result_folder = os.path.join(args.result_folder, "MOT20-val")
    elif args.dataset == "custom":
        args.result_folder = os.path.join(args.result_folder, "custom_dataset")

    if args.test_dataset:
        args.result_folder = args.result_folder.replace("-val", "-test")

    return args


def _dispatch(args):
    if args.engine == "torch":
        from src.eval_torch import evaluate_torch
        return evaluate_torch(args)
    if args.engine == "trt":
        from src.eval_trt import evaluate_trt
        return evaluate_trt(args)
    if args.engine == "trt_opt":
        from src.eval_trt_opt import evaluate_trt_opt
        return evaluate_trt_opt(args)
    raise ValueError(f"Unknown engine: {args.engine}")


def main():
    args = get_main_args()

    print(f"[main] engine={args.engine} dataset={args.dataset} exp_name={args.exp_name}")
    out = _dispatch(args)
    timer = out["timer"]
    results = out["results"]

    folder = save_mot_results(results, args.result_folder, args.exp_name)
    print(f"[main] Raw results saved to {folder}")

    if not args.no_post:
        post_start = time.time()
        post_data, post_gbi = apply_post_processing(args.result_folder, args.exp_name)
        timer.post_processing_time = time.time() - post_start
        print(f"[main] DTI applied   → {post_data}")
        print(f"[main] GBI applied   → {post_gbi}")

    timing = timer.to_dict(args.engine)

    metrics = {}
    if not args.no_eval and not args.test_dataset:
        target_map = {
            "raw": args.exp_name,
            "post": f"{args.exp_name}_post",
            "post_gbi": f"{args.exp_name}_post_gbi",
        }
        target_name = target_map[args.eval_target]
        if args.no_post and args.eval_target != "raw":
            target_name = args.exp_name  # fall back to raw when post was skipped
        eval_start = time.time()
        try:
            metrics = run_trackeval(args, target_name)
            metrics["eval_target"] = target_name
            metrics["eval_seconds"] = round(time.time() - eval_start, 2)
        except Exception as exc:  # noqa: BLE001
            print(f"[main] TrackEval failed: {exc}")
            metrics = {"error": str(exc), "eval_target": target_name}
    elif args.test_dataset:
        print("[main] --test_dataset skips TrackEval (test split has no GT).")

    combined = {"timing": timing, "metrics": metrics}
    out_path = os.path.join(args.result_folder, f"{args.exp_name}_results.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(combined, f, indent=2)
    print(f"[main] Results JSON  → {out_path}")

    print_timing_summary(timing, args.exp_name)
    if metrics and not metrics.get("error"):
        print_metrics_summary(metrics)


if __name__ == "__main__":
    main()
