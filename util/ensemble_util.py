import pandas as pd
import shutil
import re
import copy
import json
import os
import pathlib
import time
import collections

import numpy as np
import xgboost as xgb
from sklearn.metrics import fbeta_score

from util import experiment
from util import model_statistics as statis
from util import data_loader
from util import keras_util
from util import submit_util
from util import metrics
from util import path

# 根据模型1， 2， 3， 4， 9的结果统计，选择了一定范围内的阈值而非0~1之间的所有可能阈值
SPARSE_F2_THRESHOLD = []
for i in range(3, 30, 3):
    SPARSE_F2_THRESHOLD.append(i / 100)
for i in range(30, 51, 5):
    SPARSE_F2_THRESHOLD.append(i / 100)

print("f2 threshold is :", SPARSE_F2_THRESHOLD)


class EnsembleModel(object):
    """
    1. 自动做K-FOLD训练，将所有的模型都进行保存， 并将预测结果和评估结果也进行保存
    """

    def __init__(self,
                 model_path: str,
                 corr_threshold=0.9,
                 search=20,
                 top_n=5,
                 meta_model_dir=path.root_path,
                 debug=False
                 ):
        file_name = os.path.basename(model_path)
        model_dir = os.path.dirname(model_path)
        self.dataset = []
        self.corr_threshold = corr_threshold
        self.search = search
        self.top_n = top_n
        self.file_name = file_name.split(".")[0]
        self.record_dir = os.path.join(os.path.join(model_dir, "record"), file_name.split(".")[0])
        self.meta_model_dir = meta_model_dir
        self.statistics_dir = os.path.join(self.record_dir, "statistics")
        self.log_file = os.path.join(self.record_dir, "log.txt")
        self.meta_model_txt = os.path.join(self.record_dir, "meta_model.txt")
        self.meta_mode_json = os.path.join(self.record_dir, "meta_model.json")
        self.evaluate_json = os.path.join(self.record_dir, "evaluate.json")

        pathlib.Path(self.record_dir).mkdir(parents=True, exist_ok=True)
        pathlib.Path(self.statistics_dir).mkdir(parents=True, exist_ok=True)

        self.meta_model_all = self.get_meta_model()
        if self.meta_model_all is None:
            self.meta_model_all = []
            one_label_all, corr_all = statis.do_statistics(self.statistics_dir, search)

            for val_index in range(5):
                self.save_log("start search val %d" % (val_index + 1))
                one_label = one_label_all[val_index]
                meta_model_val = [[] for i in range(13)]
                for label in range(13):
                    self.save_log("start search label %d" % label)
                    _search = min(search, len(one_label[label]))
                    ignore = []
                    corr = corr_all[val_index][label].values
                    for i in range(_search):
                        for j in range(_search):
                            if corr[i, j] > corr_threshold and i not in ignore and i != j:
                                self.save_log("ignore[corr %3f] %s" % (corr[i, j], one_label[label][j][0]))
                                ignore.append(j)

                    for i in range(len(one_label[label])):
                        if i in ignore:
                            continue
                        meta_model_val[label].append(one_label[label][i])
                        if len(meta_model_val[label]) >= top_n:
                            break

                    assert len(meta_model_val[label]) == top_n

                self.meta_model_all.append(meta_model_val)

            self.save_meta_model()

            if debug:
                saved_meta_model = self.get_meta_model()
                for val in range(5):
                    for label in range(13):
                        for i in range(top_n):
                            assert self.meta_model_all[val][label][i][0] == saved_meta_model[val][label][i][0]
                            assert self.meta_model_all[val][label][i][1] == saved_meta_model[val][label][i][1]
        else:
            self.save_log("load meta model info")

        self.meta_model_statistics()

    def save_log(self, log):
        log = time.strftime("%Y-%m-%d:%H:%M:%S") + ": " + log
        print(log)
        with open(self.log_file, "a") as f:
            f.write(log)
            f.write("\n")

    def save_meta_model(self):
        with open(self.meta_model_txt, "w+") as f:
            for val in range(5):
                f.write("##############val %d###############\n" % val)
                for label in range(13):
                    f.write("--------------label %d--------------\n" % label)
                    for meta_model in self.meta_model_all[val][label]:
                        f.write("[f2 %4f]:%s\n" % (meta_model[1], meta_model[0]))
        with open(self.meta_mode_json, "w+") as f:
            json.dump(self.meta_model_all, f)

    def meta_model_statistics(self):
        models = []
        for val in self.meta_model_all:
            for label in val:
                for top_n in label:
                    models.append(top_n[0])
        model_set = set(models)
        with open(os.path.join(self.statistics_dir, "meta_model_statis.txt"), "w+") as f:
            f.write("model_number: %d\n" % len(model_set))
            for model in model_set:
                f.write("%s\n" % model)

    def get_meta_model(self):
        if not os.path.exists(self.meta_model_txt):
            return None

        with open(self.meta_mode_json, "r") as f:
            return json.load(f)

    def save_evaluate_json(self, evaluate):
        with open(self.evaluate_json, "w+") as f:
            json.dump(evaluate, f)

    def get_evaluate_json(self):
        if not os.path.exists(self.evaluate_json):
            return {}

        with open(self.evaluate_json, "r") as f:
            return json.load(f)

    def find_segmented_model(self):
        for val in self.meta_model_all:
            for label in val:
                for top_n in label:
                    meta_model_path = top_n[0]
                    unique_path = re.match(r".*competition[\\/]*(.*)", meta_model_path).group(1)
                    identifier = "-".join(unique_path.split("\\"))
                    cnn_result_path = os.path.join(path.CNN_RESULT_PATH, identifier)
                    weight_file = os.path.join(path.root_path, pathlib.Path(unique_path))
                    real_weight_file = os.path.join(self.meta_model_dir, pathlib.Path(unique_path))
                    attr_get_model, attr_model_config = keras_util.dynamic_model_import(weight_file)
                    if path.DATA_TYPE_SEGMENTED in attr_model_config.data_type:
                        print(meta_model_path)

    def get_meta_predict(self, val_index, get_segmented=False, debug=False):
        original_test_file = []
        segmented_test_file = []
        cnt = 0
        with open(path.TEST_DATA_TXT, 'r') as f:
            for i in f.readlines():
                image_name = i.split(",")[0] + ".jpg"
                original_test_file.append(os.path.join(path.ORIGINAL_TEST_IMAGES_PATH, image_name))
                segmented_test_file.append(os.path.join(path.SEGMENTED_TEST_IMAGES_PATH, image_name))

        for val in val_index:
            val_model = self.meta_model_all[val - 1]
            for label in val_model:
                for top_n in label:
                    meta_model_path = top_n[0]
                    if "ubuntu" in meta_model_path:
                        sep = "/"
                    else:
                        sep = "\\"
                    unique_path = re.match(r".*competition[\\/]*(.*)", meta_model_path).group(1)
                    identifier = "-".join(unique_path.split(sep))
                    cnn_result_path = os.path.join(path.CNN_RESULT_PATH, identifier)
                    if os.path.exists(keras_util.get_prediction_path(cnn_result_path)):
                        # self.save_log("file existed %s" % keras_util.get_prediction_path(cnn_result_path))
                        continue

                    weight_file = os.path.join(path.root_path, pathlib.Path(unique_path))
                    real_weight_file = os.path.join(self.meta_model_dir, pathlib.Path(unique_path))
                    if not os.path.exists(real_weight_file):
                        self.save_log("weight not existed, %s " % real_weight_file)
                        continue

                    if debug:
                        print(f"{weight_file}")
                        cnt += 1
                        continue

                    # self.save_log("weight file %s, real weight file %s" % (weight_file, real_weight_file))
                    attr_get_model, attr_model_config = keras_util.dynamic_model_import(weight_file)

                    if not get_segmented and path.DATA_TYPE_SEGMENTED in attr_model_config.data_type:
                        self.save_log("not train segmented model, %s" % real_weight_file)
                        continue

                    model = attr_get_model(output_dim=len(attr_model_config.label_position), weights=None)
                    model.load_weights(real_weight_file)
                    attr_model_config.val_files = []
                    for data_type in attr_model_config.data_type:
                        if data_type == path.DATA_TYPE_ORIGINAL:
                            # self.save_log("model %s use original data" % unique_path)
                            attr_model_config.val_files.append(original_test_file)
                        if data_type == path.DATA_TYPE_SEGMENTED:
                            self.save_log("model %s use segmented data" % unique_path)
                            attr_model_config.val_files.append(segmented_test_file)
                    attr_model_config.tta_flip = True
                    y_pred = keras_util.predict_tta(model, attr_model_config, verbose=1)
                    keras_util.save_prediction_file(y_pred, cnn_result_path)

        print(f"need predict {cnt} model")

    def build_datasets(self, val_index, target_label, train_label=None):
        assert len(self.meta_model_all) == 5

        train_x, val_x = None, None

        if train_label is None:
            labels = [i for i in range(13)]
        else:
            labels = train_label

        samples_cnt = 0
        for val in range(1, 6):
            meta_model_val = self.meta_model_all[val - 1]
            predict_val = None
            assert len(meta_model_val) == 13
            for label in labels:
                meta_model_label = meta_model_val[label]
                for meta_model in meta_model_label:
                    predicts = np.load(keras_util.get_prediction_path(meta_model[0]))
                    predict_label = predicts[:, label].reshape((-1, 1))
                    samples_cnt += predict_label.shape[0]
                    if predict_val is None:
                        predict_val = predict_label
                    else:
                        predict_val = np.hstack((predict_val, predict_label))

            if val == val_index:
                val_x = np.copy(predict_val)
            else:
                if train_x is None:
                    train_x = np.copy(predict_val)
                else:
                    train_x = np.vstack((train_x, predict_val))

            assert predict_val.shape[1] == len(labels) * self.top_n

        train_y, val_y = data_loader.get_k_fold_labels(val_index, target_label)

        return train_x.astype(np.float32), train_y.astype(np.float32), val_x.astype(np.float32), val_y.astype(
            np.float32)

    def get_meta_model_test_predict(self, meta_model_path):
        sep = "\\"
        if "ubuntu" in meta_model_path:
            sep = "/"
        unique_path = re.match(r".*competition[\\/]*(.*)", meta_model_path).group(1)
        identifier = "-".join(unique_path.split(sep))
        model_path = os.path.join(path.CNN_RESULT_PATH, identifier)
        predict_path = keras_util.get_prediction_path(model_path)
        try:
            assert os.path.exists(predict_path)
        except:
            print(predict_path)
        return np.load(predict_path)

    def build_test_datasets(self, cnn_avg=False):
        # 输出一个list， 包含val1~5的五份数据
        assert len(self.meta_model_all) == 5
        data_x = []
        labels = [i for i in range(13)]
        samples_cnt = 0
        for val in range(1, 6):
            meta_model_val = self.meta_model_all[val - 1]
            predict_val = None
            assert len(meta_model_val) == 13
            for label in labels:
                meta_model_label = meta_model_val[label]
                for meta_model in meta_model_label:
                    predicts = self.get_meta_model_test_predict(meta_model[0])
                    predict_label = predicts[:, label].reshape((-1, 1))
                    samples_cnt += predict_label.shape[0]
                    if predict_val is None:
                        predict_val = np.copy(predict_label)
                    else:
                        predict_val = np.hstack((predict_val, predict_label))
            data_x.append(predict_val)
            assert predict_val.shape[1] == len(labels) * self.top_n

        if cnn_avg:
            data_x_avg = data_x[0]
            for i in data_x[1:]:
                data_x_avg += i
            data_x_avg /= len(data_x)
            self.save_log("use cnn avg")
            return [data_x_avg]

        return data_x

    def build_all_datasets(self):
        assert len(self.meta_model_all) == 5
        data_x = None
        labels = [i for i in range(13)]
        samples_cnt = 0
        for val in range(1, 6):
            meta_model_val = self.meta_model_all[val - 1]
            predict_val = None
            assert len(meta_model_val) == 13
            for label in labels:
                meta_model_label = meta_model_val[label]
                for meta_model in meta_model_label:
                    predicts = np.load(keras_util.get_prediction_path(meta_model[0]))
                    predict_label = predicts[:, label].reshape((-1, 1))
                    samples_cnt += predict_label.shape[0]
                    if predict_val is None:
                        predict_val = np.copy(predict_label)
                    else:
                        predict_val = np.hstack((predict_val, predict_label))
            if data_x is None:
                data_x = np.copy(predict_val)
            else:
                data_x = np.vstack((data_x, predict_val))
            assert predict_val.shape[1] == len(labels) * self.top_n
        data_y = data_loader.get_k_fold_all_labels()
        return data_x.astype(np.float32), data_y.astype(np.float32)

    def model_rank(self, rank_n):
        for val_index in range(1, 6):
            val_model = self.meta_model_all[val_index - 1]
            model_dict = {}
            for label in val_model:
                for top_n in label:
                    if model_dict.get(top_n[0].split(".")[0]) == None:
                        model_dict[top_n[0].split(".")[0]] = 1
                    else:
                        model_dict[top_n[0].split(".")[0]] += 1

            model_sorted = sorted(model_dict.items(), key=lambda d: d[1], reverse=True)
            if val_index == 1:
                mode = "w+"
            else:
                mode = "a"
            with open(os.path.join(self.record_dir, "meta_model_rank.txt"), mode) as f:
                cnt = 0
                f.write("================================================\n")
                f.write("val %d\n" % val_index)
                for i in model_sorted:
                    f.write("%d: %s\n" % (i[1], i[0]))
                    cnt += 1
                    if cnt >= rank_n:
                        break

    def train_all_label(self):
        pass

    def train_single_label(self, val_index, label):
        pass

    def evaluate(self, y, y_pred, weight_name, xgb_param=None, save_evaluate=False):
        if y.shape[1] > 1:
            thread_f2_01 = fbeta_score(y, (np.array(y_pred) > 0.1).astype(np.int8), beta=2, average='macro')
            thread_f2_02 = fbeta_score(y, (np.array(y_pred) > 0.2).astype(np.int8), beta=2, average='macro')
        else:
            thread_f2_01 = fbeta_score(y, (np.array(y_pred) > 0.1).astype(np.int8), beta=2)
            thread_f2_02 = fbeta_score(y, (np.array(y_pred) > 0.2).astype(np.int8), beta=2)

        one_label_greedy_f2_all = []
        one_label_greedy_threshold_all = []
        one_label_smooth_f2_all = []
        assert y.shape[-1] == 1
        for i in range(y.shape[-1]):
            one_label_smooth_f2 = metrics.smooth_f2_score_np(y[:, i], y_pred[:, i])
            one_label_greedy_f2, greedy_threshold = metrics.greedy_f2_score(y[:, i], y_pred[:, i], 1)
            one_label_smooth_f2_all.append(one_label_smooth_f2)
            one_label_greedy_f2_all.append(one_label_greedy_f2)
            one_label_greedy_threshold_all.append(greedy_threshold[0])

        greedy_f2 = np.mean(one_label_greedy_f2_all)

        print("####### Smooth F2-Score is %6f #######" % np.mean(one_label_smooth_f2_all))
        print("####### F2-Score with threshold 0.1 is %6f #######" % thread_f2_01)
        print("####### F2-Score with threshold 0.2 is %6f #######" % thread_f2_02)
        print("####### Greedy F2-Score is %6f #######" % greedy_f2)

        if save_evaluate:
            evaluate = self.get_evaluate_json()
            evaluate[weight_name] = {}
            evaluate[weight_name]['eta'] = xgb_param['eta']
            evaluate[weight_name]['max_depth'] = xgb_param['max_depth']
            evaluate[weight_name]['min_child_weight'] = xgb_param['min_child_weight']
            evaluate[weight_name]['best_iteration'] = xgb_param['best_iteration']
            evaluate[weight_name]['best_ntree_limit'] = xgb_param['best_ntree_limit']
            evaluate[weight_name]['smooth_f2'] = np.mean(one_label_smooth_f2_all)
            evaluate[weight_name]['f2_0.1'] = thread_f2_01
            evaluate[weight_name]['f2_0.2'] = thread_f2_02
            evaluate[weight_name]['greedy_threshold'] = one_label_greedy_threshold_all[0]
            evaluate[weight_name]['greedy_f2'] = greedy_f2
            self.save_evaluate_json(evaluate)

        return greedy_f2

    def get_model_param(self, weight_name):
        evaluate = self.get_evaluate_json()
        param_dic = {}
        param_dic['eta'] = evaluate[weight_name]['eta']
        param_dic['max_depth'] = evaluate[weight_name]['max_depth']
        # param_dic['min_child_weight'] = evaluate[weight_name]['min_child_weight']
        param_dic['best_iteration'] = evaluate[weight_name]['best_iteration']
        param_dic['best_ntree_limit'] = evaluate[weight_name]['best_ntree_limit']
        param_dic['smooth_f2'] = evaluate[weight_name]['smooth_f2']
        param_dic['f2_0.1'] = evaluate[weight_name]['f2_0.1']
        param_dic['f2_0.2'] = evaluate[weight_name]['f2_0.2']
        param_dic['greedy_threshold'] = evaluate[weight_name]['greedy_threshold']
        param_dic['greedy_f2'] = evaluate[weight_name]['greedy_f2']
        return param_dic


def xgb_f2_metric(preds, dtrain):  # preds是结果（概率值），dtrain是个带label的DMatrix
    labels = dtrain.get_label()  # 提取label
    f2_02 = fbeta_score(labels, (np.array(preds) > 0.2).astype(np.int8), beta=2)
    return 'F2-0.2', 1 - f2_02


def xgb_greedy_f2_metric(preds, dtrain, step=100):
    labels = dtrain.get_label()  # 提取label
    greedy_f2, _ = metrics.greedy_f2_score(labels, preds, 1, step)
    return 'Greedy-F2', 1 - greedy_f2


def sparse_greedy_f2_score(y_true, y_pred):
    best_score = 0
    best_threshold = 0

    for i in SPARSE_F2_THRESHOLD:
        threshold = i
        score = fbeta_score(y_true, (np.array(y_pred) > threshold).astype(np.int8), beta=2)
        if score > best_score:
            best_score = score
            best_threshold = threshold

    return best_score, best_threshold


def xgb_sparse_greedy_f2_metric(preds, dtrain):
    labels = dtrain.get_label()  # 提取label
    greedy_f2, _ = sparse_greedy_f2_score(labels, preds)
    return 'Sparse-Greedy-F2', 1 - greedy_f2


class XGBoostModel(EnsembleModel):
    def __init__(self, xgb_param: dict = None, number_round=None, eval_func=None,
                 *args, **kwargs):
        super(XGBoostModel, self).__init__(*args, **kwargs)
        self.xgb_param = xgb_param
        self.number_round = number_round
        self.best_ntree_json = os.path.join(self.record_dir, "best_ntree.json")
        self.model_dir = os.path.join(self.record_dir, "booster")
        if eval_func is None:
            self.eval_func = xgb_f2_metric
        else:
            self.eval_func = eval_func

        pathlib.Path(self.model_dir).mkdir(parents=True, exist_ok=True)

        if self.xgb_param.get('min_child_weight', None) is None:
            self.xgb_param['min_child_weight'] = [1]
        if self.xgb_param.get('eta', None) is None:
            self.xgb_param['eta'] = [0.3]
        if self.xgb_param.get('max_depth', None) is None:
            self.xgb_param['max_depth'] = [6]

    def load_model(self, val_index, label):
        booster = xgb.Booster()
        booster.load_model(os.path.join(self.model_dir, self.get_model_name(val_index, label)))
        return booster

    def save_model(self, model, val_index, label):
        model.save_model(os.path.join(self.model_dir, self.get_model_name(val_index, label)))

    def get_model_name(self, val_index, label):
        return "ensemble_val%d_label%d.xgb" % (val_index, label)

    def model_merge(self, model_names: list):
        xgb_models = []
        for model_name in model_names:
            package = __import__(".".join(["ensemble", "xgb", model_name]))
            xgb_models.append(getattr(getattr(getattr(package, "xgb"), model_name), "model"))

        target_evaluate = copy.deepcopy(xgb_models[0].get_evaluate_json())
        for booster_name in xgb_models[0].get_evaluate_json().keys():
            target_evaluate[booster_name]["greedy_f2"] = -1
            best_xgb_model = None
            for xgb_model in xgb_models:
                evaluate = xgb_model.get_evaluate_json()
                if evaluate[booster_name]["greedy_f2"] > target_evaluate[booster_name]["greedy_f2"]:
                    target_evaluate[booster_name] = copy.deepcopy(evaluate[booster_name])
                    best_xgb_model = xgb_model
            src = os.path.join(best_xgb_model.model_dir, booster_name)
            dst = os.path.join(self.model_dir, booster_name)
            self.save_log("copy model, %s -> %s" % (src, dst))
            shutil.copy(src, dst)

        self.save_evaluate_json(target_evaluate)

    def train_all_label(self):
        for val_index in range(1, 6):
            for label in range(13):
                evaluate = self.get_evaluate_json()
                if self.get_model_name(val_index, label) not in evaluate:
                    self.train_single_label(val_index=val_index, label=label)

    def train_single_label(self, val_index, label):
        train_x, train_y, val_x, val_y = self.build_datasets(val_index=val_index, target_label=label)
        data_train = xgb.DMatrix(data=train_x, label=train_y)
        data_val = xgb.DMatrix(data=val_x, label=val_y)

        evals = [(data_train, 'train'), (data_val, 'eval')]
        best_f2 = 0
        best_eta = 0
        best_max_depth = 0
        best_model = None
        best_pred = None
        best_xgb_param = None
        best_min_child_weight = None

        for eta in self.xgb_param["eta"]:
            for max_depth in self.xgb_param['max_depth']:
                for min_child_weight in self.xgb_param['min_child_weight']:
                    xgb_param = {
                        'eta': eta,
                        'silent': self.xgb_param['silent'],  # option for logging
                        'objective': self.xgb_param['objective'],  # error evaluation for multiclass tasks
                        'max_depth': max_depth,  # depth of the trees in the boosting process
                        'min_child_weight': min_child_weight,
                        'nthread': 4
                    }

                    bst = xgb.train(xgb_param, data_train, self.number_round, evals=evals,
                                    feval=self.eval_func,
                                    early_stopping_rounds=10)

                    data_eva = xgb.DMatrix(val_x)
                    ypred = bst.predict(data_eva, ntree_limit=bst.best_ntree_limit)
                    ypred = ypred.reshape((-1, 1))
                    f2 = self.evaluate(y_pred=ypred, y=val_y, weight_name=self.get_model_name(val_index, label))
                    self.save_log("eta:%f, max_depth:%d, f2:%f" % (eta, max_depth, f2))
                    self.save_log("best_iteration:%4f,  best_score:%4f, best_ntree_limit=%4f" % (bst.best_iteration,
                                                                                                 bst.best_score,
                                                                                                 bst.best_ntree_limit))
                    self.save_log("\n")

                    if f2 > best_f2:
                        best_f2 = f2
                        best_model = bst
                        best_eta = eta
                        best_max_depth = max_depth
                        best_min_child_weight = min_child_weight
                        best_pred = ypred
                        best_xgb_param = copy.deepcopy(xgb_param)
                        best_xgb_param['best_ntree_limit'] = bst.best_ntree_limit
                        best_xgb_param['best_iteration'] = bst.best_iteration

        self.evaluate(y_pred=best_pred, y=val_y, weight_name=self.get_model_name(val_index, label),
                      xgb_param=best_xgb_param, save_evaluate=True)

        self.save_log(
            "save best model for val[%d] label[%d], f2[%f] eta[%f] max_depth[%d]  best_min_child_weight[%f] best_ntree[%d] best_iter[%d]" %
            (val_index, label, best_f2, best_eta, best_max_depth, best_min_child_weight,
             best_xgb_param['best_ntree_limit'],
             best_xgb_param['best_iteration']))

        self.save_model(best_model, val_index, label)

        # 测试load_model是否正确
        model = self.load_model(val_index, label)
        data_eva = xgb.DMatrix(val_x)
        ypred = model.predict(data_eva, ntree_limit=best_xgb_param['best_ntree_limit'])
        ypred = ypred.reshape((-1, 1))
        f2 = self.evaluate(y_pred=ypred, y=val_y, weight_name=self.get_model_name(val_index, label))
        assert abs((f2 - best_f2) / f2) < 0.001

    def predict_all_label(self, data):
        '''
        :param data:
        :return:[](13, 5),每一个元素是每个模型的预测结果array
        '''
        data = xgb.DMatrix(data)
        predict_dic = []
        for label in range(13):
            predict_dic.append([])
            for val_index in range(1, 6):
                predict_dic[label].append([])
                model_name = self.get_model_name(val_index, label)
                model_param = self.get_model_param(model_name)
                predict_dic[label][val_index - 1] = self.predict_one_label(val_index, label, data,
                                                                           model_param['best_ntree_limit'])
        assert len(predict_dic) == 13
        assert len(predict_dic[0]) == 5
        for i in range(13):
            for j in range(5):
                assert predict_dic[i][j].shape == predict_dic[0][0].shape
        return predict_dic

    def predict_one_label(self, val_index, label, data, ntree_limit):
        bst = self.load_model(val_index, label)
        data_pred = bst.predict(data, ntree_limit=ntree_limit)
        return data_pred

    def get_test_labels(self):
        labels = []
        with open(path.TEST_RESULT_TXT, "r") as f:
            for i in f.readlines():
                result = i.strip().split(",")[1:]
                result = [int(c) for c in result]
                labels.append(result)

        return np.array(labels, np.int8)

    def get_statistic_json(self, name):
        with open(name, "r") as f:
            return json.load(f)

    def save_statistic_json(self, name, dic):
        with open(name, "w+") as f:
            json.dump(dic, f)

    def build_ensemble_cv(self):
        evaluate = self.get_evaluate_json()
        ensemble_cv = self.get_statistic_json(path.ENSEMBLE_CV)
        f2 = collections.defaultdict(int)
        f2["avg"] = 0
        for key in evaluate.keys():
            label = re.search(r".*label([0-9].*).xgb", key).group(1)
            f2[label] += evaluate[key]["greedy_f2"]
        for i in range(13):
            label = f"{i}"
            f2[label] /= 5
            f2["avg"] += f2[label]
        f2["avg"] /= 13
        ensemble_cv[f"xgb_{self.file_name}"] = f2
        self.save_statistic_json(path.ENSEMBLE_CV, ensemble_cv)

    def build_cnn_ensemble(self):
        y_true_test = experiment.get_test_labels()

        f2_cv = {"avg": 0}
        f2_test = {"avg": 0}
        for i in range(13):
            f2_cv[f"{i}"] = 0
            f2_test[f"{i}"] = 0
        y_pred_test_vote = None

        meta_model = self.get_meta_model()
        for val in range(5):
            model_val = meta_model[val]
            y_pred_cv_vote = None
            for label in range(13):
                model_label = model_val[label]
                for single_model in model_label:
                    weight_path = single_model[0]
                    y_pred_cv = np.load(keras_util.get_prediction_path(weight_path))
                    y_pred_test = self.get_meta_model_test_predict(weight_path)
                    epoch_name = experiment.get_epoch_identifier(weight_path)
                    thresholds = experiment.get_threshold_cv()[epoch_name]

                    y_pred_test[:, label] = y_pred_test[:, label] > thresholds[f"{label}"]
                    y_pred_cv[:, label] = y_pred_cv[:, label] > thresholds[f"{label}"]

                    y_pred_test = y_pred_test.astype(np.int16)
                    y_pred_cv = y_pred_cv.astype(np.int16)

                    if y_pred_cv_vote is None:
                        y_pred_cv_vote = np.zeros(y_pred_cv.shape)
                    if y_pred_test_vote is None:
                        y_pred_test_vote = np.zeros(y_pred_test.shape)

                    y_pred_cv_vote[:, label] += y_pred_cv[:, label]
                    y_pred_test_vote[:, label] += y_pred_test[:, label]



            y_pred_cv_vote = y_pred_cv_vote > self.top_n / 2
            y_pred_cv_vote = y_pred_cv_vote.astype(np.int8)
            _, y_true_cv = data_loader.get_k_fold_labels(val + 1)

            for i in range(13):
                f2_cv[f"{i}"] += fbeta_score(np.array(y_true_cv[:, i]), y_pred_cv_vote[:, i], beta=2)

        y_pred_test_vote = y_pred_test_vote > (self.top_n * 5) / 2
        y_pred_test_vote = y_pred_test_vote.astype(np.int8)

        for i in range(13):
            f2_test[f"{i}"] += fbeta_score(y_true_test[:, i], y_pred_test_vote[:, i], beta=2)

        ensemble_test = self.get_statistic_json(path.ENSEMBLE_TEST)
        ensemble_cv = self.get_statistic_json(path.ENSEMBLE_CV)

        for i in range(13):
            f2_cv[f"{i}"] /= 5
            f2_cv['avg'] += f2_cv[f"{i}"]
            f2_test['avg'] += f2_test[f"{i}"]

        f2_cv['avg'] /= 13
        f2_test['avg'] /= 13

        ensemble_test[f"cnn_{self.file_name}.txt"] = f2_test
        ensemble_cv[f"cnn_{self.file_name}.txt"] = f2_cv

        self.save_statistic_json(path.ENSEMBLE_TEST, ensemble_test)
        self.save_statistic_json(path.ENSEMBLE_CV, ensemble_cv)

    def build_and_predict_test(self):
        y_true = self.get_test_labels()
        ensemble_test = self.get_statistic_json(path.ENSEMBLE_TEST)

        test_x = self.build_test_datasets(cnn_avg=True)
        # output_avg表示是是否对xgboost同一个模型输出的多个数据进行平均
        pre_y = self.predict_test(test_x, xgb_avg=False)
        np.save(os.path.join(path.XGB_RESULT_PATH, "xgb_%s_avg[cnn].npy" % self.file_name), pre_y)
        submit_util.save_submit(pre_y, "xgb_%s_avg[cnn].txt" % self.file_name)
        f2 = {"avg": 0}
        for i in range(13):
            f2[f"{i}"] = fbeta_score(y_true[:, i], pre_y[:, i], beta=2)
            f2["avg"] += f2[f"{i}"]
            self.save_log(f"label {i}: %f" % f2[f"{i}"])
        f2["avg"] /= 13
        self.save_log(f"average: %f" % f2["avg"])
        ensemble_test[f"xgb_{self.file_name}_avg[cnn].txt"] = f2

        test_x = self.build_test_datasets(cnn_avg=False)
        # output_avg表示是是否对xgboost同一个模型输出的多个数据进行平均
        pre_y = self.predict_test(test_x, xgb_avg=True)
        np.save(os.path.join(path.XGB_RESULT_PATH, "xgb_%s_avg[xgb].npy" % self.file_name), pre_y)
        submit_util.save_submit(pre_y, "xgb_%s_avg[xgb].txt" % self.file_name)
        f2 = {"avg": 0}
        for i in range(13):
            f2[f"{i}"] = fbeta_score(y_true[:, i], pre_y[:, i], beta=2)
            f2["avg"] += f2[f"{i}"]
            self.save_log(f"label {i}: %f" % f2[f"{i}"])
        f2["avg"] /= 13
        self.save_log(f"average: %f" % f2["avg"])
        ensemble_test[f"xgb_{self.file_name}_avg[xgb].txt"] = f2

        # output_avg表示是是否对xgboost同一个模型输出的多个数据进行平均
        pre_y = self.predict_test(test_x, xgb_avg=False)
        np.save(os.path.join(path.XGB_RESULT_PATH, "xgb_%s.npy" % self.file_name), pre_y)
        submit_util.save_submit(pre_y, "xgb_%s.txt" % self.file_name)
        f2 = {"avg": 0}
        for i in range(13):
            f2[f"{i}"] = fbeta_score(y_true[:, i], pre_y[:, i], beta=2)
            f2["avg"] += f2[f"{i}"]
            self.save_log(f"label {i}: %f" % f2[f"{i}"])
        f2["avg"] /= 13
        self.save_log(f"average: %f" % f2["avg"])
        ensemble_test[f"xgb_{self.file_name}.txt"] = f2

        self.save_statistic_json(path.ENSEMBLE_TEST, ensemble_test)

    def predict_test(self, data_list, xgb_avg=False, mode='vote'):
        predicts = []
        # 针对多个输入的数据分别做预测
        for data in data_list:
            pred = copy.deepcopy(self.predict_all_label(data))
            predicts.append(pred)

        xgb_result = [[] for i in range(5)]
        result = np.zeros((13, len(data_list[0])))

        if mode == 'vote':
            if xgb_avg:
                # 针对多个输入对应的输出做平均
                self.save_log("use xgb avg")
                xgb_pred_avg = predicts[0]

                for predict in predicts[1:]:
                    for val_index in range(5):
                        for label in range(13):
                            xgb_pred_avg[label][val_index] += predict[label][val_index]

                for val_index in range(5):
                    for label in range(13):
                        xgb_pred_avg[label][val_index] /= 5

                for val_index in range(5):
                    for label in range(13):
                        model_name = self.get_model_name(val_index + 1, label)
                        model_param = self.get_model_param(model_name)
                        xgb_pred_avg[label][val_index] = np.where(
                            xgb_pred_avg[label][val_index] > model_param['greedy_threshold'], 1, -1).reshape((1, -1))
                        if len(xgb_result[val_index]) == 0:
                            xgb_result[val_index] = np.copy(xgb_pred_avg[label][val_index])
                        else:
                            xgb_result[val_index] = np.vstack((xgb_result[val_index], xgb_pred_avg[label][val_index]))

                for val_index in range(5):
                    for label in range(13):
                        result[label] += xgb_result[val_index][label]
            else:
                # 不对多个输入对应的输出做平均，而是全部一起进行投票
                for xgb_pred in predicts:
                    for val_index in range(5):
                        for label in range(13):
                            model_name = self.get_model_name(val_index + 1, label)
                            model_param = self.get_model_param(model_name)
                            xgb_pred[label][val_index] = np.where(
                                xgb_pred[label][val_index] > model_param['greedy_threshold'], 1, -1).reshape((1, -1))
                            if len(xgb_result[val_index]) == 0:
                                xgb_result[val_index] = np.copy(xgb_pred[label][val_index])
                            else:
                                xgb_result[val_index] = np.vstack((xgb_result[val_index], xgb_pred[label][val_index]))

                    for val_index in range(5):
                        for label in range(13):
                            result[label] += xgb_result[val_index][label]

            result = np.where(result > 0, 1, 0)

            return result.transpose()

    def predict_real(self, data, mode='vote'):
        xgb_pred = copy.deepcopy(self.predict_all_label(data))
        xgb_result = [[] for i in range(5)]
        if mode == 'vote':
            for val_index in range(5):
                for label in range(13):
                    model_name = self.get_model_name(val_index + 1, label)
                    model_param = self.get_model_param(model_name)
                    xgb_pred[label][val_index] = np.where(
                        xgb_pred[label][val_index] > model_param['greedy_threshold'], 1, -1).reshape((1, -1))
                    if len(xgb_result[val_index]) == 0:
                        xgb_result[val_index] = np.copy(xgb_pred[label][val_index])
                    else:
                        xgb_result[val_index] = np.vstack((xgb_result[val_index], xgb_pred[label][val_index]))
            result = np.zeros((13, len(xgb_result[0][0])))
            for val_index in range(5):
                for label in range(13):
                    result[label] += xgb_result[val_index][label]

            result = np.where(result > 0, 1, 0)
            return result.transpose()

    def predict_real_f2(self, data_x, data_y, mode='vote'):
        pre_y = self.predict_real(data_x, mode=mode)
        record_file = os.path.join(self.record_dir, "predict_real_f2_score.txt")
        f2_score = []
        for i in range(13):
            f2_score.append(fbeta_score(data_y[:, i], pre_y[:, i], beta=2))
        with open(record_file, 'w') as f:
            for i in range(13):
                f.write("Label%d f2_score: %s\n" % (i, str(f2_score[i])))
            f.write('=' * 20)
            f.write('\n')
            f.write("Total f2_score: %s\n" % str(sum(f2_score) / 13))
        print("==========predict_real_f2 SUCCESS==========")
        return pre_y

    def save_submit(self, predicts, name):
        from util import submit_util
        submit_util.save_submit(predicts, name=name)

    def statistics(self, file_names: list, save_file):
        df = pd.DataFrame()
        statis_path = os.path.join(path.XGB_RESULT_PATH, "statistics")
        pathlib.Path(statis_path).mkdir(parents=True, exist_ok=True)

        with open(os.path.join(statis_path, save_file + ".txt"), "w+") as f:
            for label in range(13):
                for file_name in file_names:
                    model_path = os.path.join(path.XGB_RESULT_PATH, file_name)
                    predict = np.load(model_path)
                    predict = predict[:, [label]]
                    df[file_name] = predict.flatten()
                    f.write("=========%s=========\n" % file_name)

                    pred_label = predict[:, 0]
                    f.write("label %d positive number: %d\n" % (label, pred_label[pred_label > 0].size))

                corr = df.corr()
                statis.heap_map(corr, statis_path, save_file + f"label_{label}" + ".png")


if __name__ == '__main__':
    pass
