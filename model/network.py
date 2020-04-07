#!/usr/bin/env python3

# Copyright 2020 The Johns Hopkins University (author: Jiatong Shi, Hailan Lin)

# debug only
import sys
sys.path.append("/Users/jiatongshi/projects/svs_system/SVS_system")

import torch
import torch.nn as nn
import numpy as np
import math
import model.module as module


class Encoder(nn.Module):
    """
    Encoder Network
    """
    def __init__(self, phone_size, embed_size, hidden_size, num_layers=1, dropout=0.1, glu_kernel=3, GLU_num=3):
        """
        :param para: dictionary that contains all parameters
        """
        super(Encoder, self).__init__()
        
        self.emb_phone = nn.Embedding(phone_size, embed_size)
        #full connected
        self.fc_1 = nn.Linear(embed_size, hidden_size)
        
        self.GLU_list = nn.ModuleList()
        for i in range(GLU_num):
            self.GLU_list.append(module.GLU(num_layers, hidden_size, glu_kernel, dropout, hidden_size))
        #self.GLU = module.GLU(num_layers, hidden_size, glu_kernel, dropout, hidden_size)
        
        self.fc_2 = nn.Linear(hidden_size, embed_size)

    def forward(self, x):
        """
        input dim: [batch_size, text_phone_length]
        output dim : [batch_size, text_phone_length, embedded_dim]
        """
        embedded_phone = self.emb_phone(x)
        glu_data = self.fc_1(embedded_phone)
        
        '''这里记得改一下！'''
        for glu in self.GLU_list:
            glu_data = glu(glu_data)
        
        glu_out = self.fc_2(torch.transpose(glu_data, 1, 2))
        
        out = embedded_phone + glu_out
        
        out = out * math.sqrt(0.5)
        return out


class Encoder_Postnet(nn.Module):
    """
    Encoder Postnet
    """
    def __init__(self, embedded_dim):
        super(Encoder_Postnet, self).__init__()

        self.fc_pitch = nn.Linear(1, embedded_dim)
        self.fc_pos = nn.Linear(1, embedded_dim)
        self.fc_beats = nn.Linear(1, embedded_dim)
    
    def aligner(self, encoder_out, align_phone, text_phone):
        '''
        align_phone = [batch_size, align_phone_length]
        text_phone = [batch_size, text_phone_length]
        align_phone_length( = frame_num) > text_phone_length
        '''
        for i in range(align_phone.shape[0]):
            before_text_phone = 0
            encoder_ind = 0
            for j in range(align_phone.shape[1]):
                if align_phone[i][j] == before_text_phone:
                    temp = encoder_out[i][encoder_ind]
                    line = torch.cat((line,temp.unsqueeze(0)),dim = 0)
                else:
                    if j == 0:
                        line = encoder_out[i][encoder_ind].unsqueeze(0)
                        before_text_phone = text_phone[i][j]
                    else:
                        encoder_ind += 1
                        before_text_phone = text_phone[i][encoder_ind]
                        temp = encoder_out[i][encoder_ind]
                        line = torch.cat((line,temp.unsqueeze(0)),dim = 0)
            if i == 0:
                out = line.unsqueeze(0)
            else:
                out = torch.cat((out,line.unsqueeze(0)),dim = 0)

        return out

    def forward(self, encoder_out, align_phone, text_phone, pitch, beats):
        """
        pitch/beats : [batch_size, frame_num] -> [batch_size, frame_num，1]
        """
        batch_size = pitch.shape[0]
        frame_num = pitch.shape[1]
        embedded_dim = encoder_out.shape[2]

        aligner_out = self.aligner(encoder_out, align_phone, text_phone)

        #pitch = pitch.type(torch.LongTensor)
        #print(aligner_out, pitch, pitch.type())
        pitch = self.fc_pitch(pitch.unsqueeze(0))
        #print(pitch, type(pitch))
        out = aligner_out + pitch

        beats = self.fc_beats(beats.unsqueeze(0))
        out = out + beats

        pos = module.PositionalEncoding(embedded_dim)
        pos_out = self.fc_pos(pos(torch.transpose(aligner_out, 0, 1)))
        out = out + torch.transpose(pos_out,0,1)

        return out
    

class Decoder(nn.Module):
    """
    Decoder Network
    """
    # TODO： frame smoothing (triple the time resolution)
    def __init__(self, num_block, hidden_size, output_dim, nhead=4, dropout=0.1, activation="relu",
        glu_kernel=3):
        super(Decoder, self).__init__()
        self.input_norm = module.LayerNorm(hidden_size)
        decoder_layer = module.TransformerGLULayer(hidden_size, nhead, dropout, activation,
            glu_kernel)
        self.decoder = module.TransformerEncoder(decoder_layer, num_block)
        self.output_fc = nn.Linear(hidden_size, output_dim)

        self.hidden_size=hidden_size

    def forward(self, src, src_mask=None, src_key_padding_mask=None):
        src = self.input_norm(src)
        memory, att_weight = self.decoder(src, mask=src_mask, src_key_padding_mask=src_key_padding_mask)
        output = self.output_fc(memory)
        return output, att_weight

class GLU_Transformer(nn.Module):
    """
    Transformer Network
    """
    def __init__(self, phone_size, embed_size, hidden_size, glu_num_layers, dropout, dec_num_block,
                 dec_nhead, output_dim):
        super(GLU_Transformer, self).__init__()
        self.encoder = Encoder(phone_size, embed_size, hidden_size, glu_num_layers, dropout)
        self.enc_postnet = Encoder_Postnet(embed_size)
        # TODO: standard input arguments
        self.decoder = Decoder(dec_num_block, embed_size, output_dim, dec_nhead, dropout)
        self.postnet = module.PostNet(output_dim, output_dim, output_dim)

    def forward(self, characters, phone, pitch, beat, pos_text=True, src_key_padding_mask=None,
                char_key_padding_mask=None):
        # TODO add encoder and encoder postnet
        memory = self.encoder(characters)
        mel_output, att_weight = self.decoder(memory)
        mel_output = self.postnet(mel_output)
        return mel_output


def create_src_key_padding_mask(src_len, max_len):
    bs = len(src_len)
    mask = np.zeros((bs, max_len))
    for i in range(bs):
        mask[i, src_len[i]:] = 1
    return torch.from_numpy(mask).float()


def _test():
    # debug test

    import random
    random.seed(7)
    batch_size = 16
    max_length = 500
    char_max_length = 50
    feat_dim = 1324
    phone_size = 67
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    seq_len_list = []
    for i in range(batch_size):
        seq_len_list.append(random.randint(0, max_length))

    char_seq_len_list = []
    for i in range(batch_size):
        char_seq_len_list.append(random.randint(0, char_max_length))

    spec = torch.zeros(batch_size, max_length, feat_dim)
    phone = torch.zeros(batch_size, max_length, 1).long()
    pitch = torch.zeros(batch_size, max_length, 1).long()
    beat = torch.zeros(batch_size, max_length, 1).long()
    char = torch.zeros([batch_size, char_max_length, 1]).long()
    for i in range(batch_size):
        length = seq_len_list[i]
        char_length = char_seq_len_list[i]
        spec[i, :length, :] = torch.randn(length, feat_dim)
        phone[i, :length, :] = torch.randint(0, phone_size, (length, 1)).long()
        pitch[i, :length, :] = torch.randint(0, 200, (length, 1)).long()
        beat[i, :length, :] = torch.randint(0, 2, (length, 1)).long()
        char[i, :char_length, :] = torch.randint(0, phone_size, (char_length, 1)).long()

    seq_len = torch.from_numpy(np.array(seq_len_list)).to(device)
    char_seq_len = torch.from_numpy(np.array(char_seq_len_list)).to(device)
    spec = spec.to(device)
    phone = phone.to(device)
    pitch = pitch.to(device)
    beat = beat.to(device)
    print(seq_len.size())
    print(char_seq_len.size())
    print(spec.size())
    print(phone.size())
    print(pitch.size())
    print(beat.size())
    print(type(beat))

    hidden_size = 256
    embed_size = 256
    nhead = 4
    dropout = 0.1
    activation = 'relu'
    glu_kernel = 3
    num_dec_block = 3
    glu_num_layers = 1
    # test model as a whole
    # model = GLU_Transformer(phone_size, hidden_size, embed_size, glu_num_layers, dropout, num_dec_block, nhead, feat_dim)
    # spec_pred = model(char, phone, pitch, beat, src_key_padding_mask=seq_len, char_key_padding_mask=char_seq_len)
    # print(spec_pred)

    # test decoder
    out_from_encoder = torch.zeros(batch_size, max_length, hidden_size)
    for i in range(batch_size):
        length = seq_len_list[i]
        out_from_encoder[i, :length, :] = torch.randn(length, hidden_size)
    decoder = Decoder(num_dec_block, embed_size, feat_dim, nhead, dropout)
    decoder_out, att = decoder(out_from_encoder, src_key_padding_mask=seq_len)
    print(decoder_out.size())
    print(att.size())



if __name__ == "__main__":
    _test()



