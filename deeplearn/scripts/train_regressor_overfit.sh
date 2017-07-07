#!/usr/bin/env bash
CONFIGPATH="../yamls/regression_jul3_strands_augmented_config"
momma_dragonn_train --valid_data_loader_config\
 $CONFIGPATH/valid_data_loader_config_rep2only.yaml\
 --evaluator_config $CONFIGPATH/evaluator_config.yaml\
 --end_of_epoch_callbacks_config $CONFIGPATH/end_of_epoch_callbacks_config.yaml\
 --end_of_training_callbacks_config $CONFIGPATH/end_of_training_callbacks_config.yaml\
 --hyperparameter_configs_list $CONFIGPATH/hyperparameter_configs_list_rep2overfit.yaml
