import os
import signal
import deeplift
import numpy as np
import deeplift.backend as B
import theano
import theano.tensor.signal.conv
import h5py
import traceback
def create_detector_from_subset_of_sequential_layers(sequential_container,
                                                    idx_of_layer_of_interest,
                                                    channel_indices,
                                                    multipliers_on_channels):
    layers = []  
    #this adds in all the layers preceeding idx_of_layer_of_interest
    #(remember zero-based indexing...)
    for layer_idx in range(idx_of_layer_of_interest):
        layers.append(
         sequential_container.get_layers()[layer_idx].copy_blob_keep_params()
        )
    #add in the layer of interest, but with the channels subsetted to
    #the channels of interest
    layer_to_subset = sequential_container.get_layers()\
                       [idx_of_layer_of_interest]
    assert hasattr(layer_to_subset, "W"), "Layer does not have weights - "\
        +" make sure you have supplied the correct index for the conv layer?"
    subsetted_weights = layer_to_subset.W[channel_indices]
    subsetted_biases = layer_to_subset.b[channel_indices]
    layer_kwargs = layer_to_subset.get_yaml_compatible_object_kwargs()
    layer_kwargs['W'] = subsetted_weights 
    layer_kwargs['b'] = subsetted_biases
    subsetted_layer = layer_to_subset.\
                      load_blob_from_yaml_contents_only(**layer_kwargs)
    layers.append(subsetted_layer)
    #check if the next layer is an activation layer
    layer_after_layer_of_interest =\
      sequential_container.get_layers()[idx_of_layer_of_interest+1]
    if isinstance(layer_after_layer_of_interest, deeplift.blobs.Activation):
        layers.append(layer_after_layer_of_interest.copy_blob_keep_params())
    #multipliers_layer = sequential_container.get_layers()[layer_idx+1].copy_blob_keep_params()
    #add in a layer with a conv filter that is the multipliers
    #need to be reversed because this is doing a convolution, not cross corr
    multipliers_layer = deeplift.blobs.Conv2D(
                    name="multipliers_layer",
                    W=multipliers_on_channels[:,:,::-1,::-1].astype('float32'),
                    b=np.zeros(multipliers_on_channels.shape[0])\
                      .astype('float32'),
                    strides=(1,1), 
                    border_mode=B.BorderMode.valid)
    layers.append(multipliers_layer)
    deeplift.util.connect_list_of_layers(layers)
    layers[-1].build_fwd_pass_vars()
    model_to_return = deeplift.models.SequentialModel(layers=layers)
    model_to_return.get_layers()
    return model_to_return
def get_conv_out_symbolic_var(input_var,
                              set_of_2d_patterns_to_conv_with,
                              normalise_by_magnitude,
                              take_max,
                              mode='full'):
    assert len(set_of_2d_patterns_to_conv_with.shape)==3
    if (normalise_by_magnitude):
        set_of_2d_patterns_to_conv_with =\
         set_of_2d_patterns_to_conv_with/\
          (np.sqrt(np.sum(np.sum(np.square(set_of_2d_patterns_to_conv_with),
                               axis=-1),
                        axis=-1))[:,None,None])
    set_of_2d_patterns_to_conv_with = np.expand_dims(set_of_2d_patterns_to_conv_with, 1)
    filters = theano.tensor.as_tensor_variable(
               x=set_of_2d_patterns_to_conv_with,
               name="filters")
    conv_out = theano.tensor.nnet.conv2d(
                input=input_var,
                filters=filters,
                border_mode=mode)
    if (normalise_by_magnitude):
        sum_squares_per_pos =\
                   theano.tensor.nnet.conv2d(
                    input=theano.tensor.square(input_var),
                    filters=np.ones(set_of_2d_patterns_to_conv_with.shape)\
                            .astype("float32"),
                    border_mode=mode) 
        per_pos_magnitude = theano.tensor.sqrt(sum_squares_per_pos)
        per_pos_magnitude += 0.0000001*(per_pos_magnitude < 0.0000001)
        conv_out = conv_out/per_pos_magnitude
    if (take_max):
        conv_out = theano.tensor.max(
                    theano.tensor.max(conv_out, axis=-1), #max over cols
                    axis=-1) #max over rows
    return conv_out 
def compile_conv_func_with_theano(set_of_2d_patterns_to_conv_with,
                                  normalise_by_magnitude=False,
                                  take_max=False,
                                  mode='full'):
    #  input_var = theano.tensor.TensorType(dtype=theano.config.floatX,
                                         #  broadcastable=[False]*3)("input")
    input_var = theano.tensor.TensorType(dtype=theano.config.floatX,
                                         broadcastable=[False, True, False, False])("input")
    conv_out = get_conv_out_symbolic_var(input_var,
                                 set_of_2d_patterns_to_conv_with,
                                 normalise_by_magnitude=normalise_by_magnitude,
                                 take_max=take_max,
                                 mode=mode)
    func = theano.function([input_var],
                           conv_out,
                           allow_input_downcast=True)
    return func 
def get_max_cross_corr(filters, things_to_scan,
                           verbose=True, batch_size=10,
                           func_params_size=1000000,
                           progress_update=1000,
                           min_overlap=0.3):
    """
        func_params_size: when compiling functions
    """
    #reverse the patterns as the func is a conv not a cross corr
    filters = filters.astype("float32")[:,::-1,::-1]
    to_return = np.zeros((filters.shape[0], len(things_to_scan)))
    #compile the number of filters that result in a function with
    #params equal to func_params_size 
    params_per_filter = np.prod(filters[0].shape)
    filter_batch_size = int(func_params_size/params_per_filter)
    filter_length = filters.shape[-1]
    filter_idx = 0 
    while filter_idx < filters.shape[0]:
        if (verbose):
            print("On filters",filter_idx,"to",(filter_idx+filter_batch_size))
        filter_batch = filters[filter_idx:(filter_idx+filter_batch_size)]
        cross_corr_func = compile_conv_func_with_theano(
                           set_of_2d_patterns_to_conv_with=filter_batch,
                           normalise_by_magnitude=False,
                           take_max=True)  
        padding_amount = int((filter_length)*(1-min_overlap))
        padded_input = np.expand_dims(np.array([np.pad(array=x,
                                                       pad_width=((padding_amount, padding_amount)),
                                                       mode="constant") for x in things_to_scan]), axis=1)
        max_cross_corrs = np.array(deeplift.util.run_function_in_batches(
                            func=cross_corr_func,
                            input_data_list=[padded_input],
                            batch_size=batch_size,
                            progress_update=(None if verbose==False else
                                             progress_update)))
        assert len(max_cross_corrs.shape)==2, max_cross_corrs.shape
        to_return[filter_idx:
                  (filter_idx+filter_batch_size),:] =\
                  np.transpose(max_cross_corrs)
        filter_idx += filter_batch_size
        
    return to_return
def get_full_cross_corr(filters, things_to_scan,
                           verbose=True, batch_size=10,
                           func_params_size=1000000,
                           progress_update=1000,
                           min_overlap=1,
                           mode='valid'):
    """
        func_params_size: when compiling functions
    """
    #reverse the patterns as the func is a conv not a cross corr
    filters = filters.astype("float32")[:,::-1,::-1]
    # padding_amount0 = int((filters[0].shape[-1])*(1-min_overlap))
    if mode == 'valid':
      num_xcor_sites = things_to_scan[0].shape[-1]-filters[0].shape[-1]+1
    elif mode == 'full':
      num_xcor_sites = things_to_scan[0].shape[-1]+filters[0].shape[-1]-1      
    to_return = np.zeros((filters.shape[0], len(things_to_scan), num_xcor_sites))
    #compile the number of filters that result in a function with
    #params equal to func_params_size 
    params_per_filter = np.prod(filters[0].shape)
    filter_batch_size = int(func_params_size/params_per_filter)
    filter_length = filters.shape[-1]
    filter_idx = 0 
    while filter_idx < filters.shape[0]:
        if (verbose):
            print("On filters",filter_idx,"to",(filter_idx+filter_batch_size))
        filter_batch = filters[filter_idx:(filter_idx+filter_batch_size)]
        cross_corr_func = compile_conv_func_with_theano(
                           set_of_2d_patterns_to_conv_with=filter_batch,
                           normalise_by_magnitude=False,
                           take_max=False,
                           mode=mode) 
        padding_amount = int((filter_length)*(1-min_overlap))
        padded_input = [np.pad(array=x,
                              pad_width=((padding_amount, padding_amount)),
                              mode="constant") for x in things_to_scan]
        all_cross_corrs = np.array(deeplift.util.run_function_in_batches(
                            func=cross_corr_func,
                            input_data_list=[padded_input],
                            batch_size=batch_size,
                            progress_update=(None if verbose==False else
                                             progress_update)))
        all_cross_corrs_max = all_cross_corrs.max(axis=2)
        assert len(all_cross_corrs_max.shape)==3, all_cross_corrs_max.shape
        to_return[filter_idx:
                  (filter_idx+filter_batch_size),:,:] =\
                  np.transpose(all_cross_corrs_max, axes=[1,0,2])
        filter_idx += filter_batch_size
        
    return to_return
